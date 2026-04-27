"""SL/TP expectancy analytics for closed crypto paper trades.

What this is
------------
Read-only analytics over ``crypto_paper_trades`` (status = closed).
Computes the realized expectancy curve and surfaces what the
SL/TP brackets actually did. Surfaces in the AdminPanel as a
"data suggests..." tile — no auto-tune, no write paths.

Why no synthetic OHLC replay
----------------------------
A true bracket replay needs intra-trade tick data to know whether
a tighter SL would have triggered before the actual exit. We
don't store tick data per closed trade, so a "would have hit"
replay would be a model dressed as a measurement. Instead we
report only what the realized data can prove:

  1.  **Realized expectancy** — mean r_multiple across all
      closed trades (the single number the strategy lives or
      dies by).
  2.  **Close-reason mix** — how often TPs fire vs SLs vs
      manual/time exits, and what each pays on average. If TPs
      pay less than SLs cost, the brackets are mis-tuned.
  3.  **Tighter-bracket what-ifs** — for trades that hit TP, a
      *tighter* TP at fraction f∈(0,1) of the ladder distance
      would have closed at f×realized_r. For trades that hit SL,
      a *tighter* SL at fraction f would have closed at -f×R.
      Wider brackets we cannot infer without OHLC, so we don't.

Output layout
-------------
    {
      "window_days": int,
      "closed_trades": int,
      "expectancy_r": float,
      "win_rate": float,
      "by_close_reason": [{reason, count, avg_r}],
      "by_direction":    [{direction, count, avg_r, win_rate}],
      "by_regime":       [{regime, count, avg_r, win_rate}],
      "tp_tightening":   [{factor: 0.50, projected_expectancy_r}, ...],
      "sl_tightening":   [{factor: 0.50, projected_expectancy_r}, ...],
      "headline":        "human-readable single-sentence summary",
    }

Sample-size guard: returns ``{"closed_trades": 0, ...}`` when the
window contains fewer than 20 closed trades — the curves are
noise below that threshold and shouldn't drive any decision.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from statistics import mean
from typing import Any

# Below this, the numbers are too noisy to surface as guidance.
_MIN_SAMPLES = 20

# Tightening grid — how much of the original bracket distance to
# keep. 1.0 = baseline, 0.50 = half as wide, etc. We only go
# tighter (wider needs OHLC).
_TIGHTEN_GRID = (0.30, 0.50, 0.75, 1.00)


def _safe_mean(values: list[float]) -> float:
    return round(mean(values), 4) if values else 0.0


def _win_rate(rs: list[float]) -> float:
    if not rs:
        return 0.0
    return round(sum(1 for r in rs if r > 0) / len(rs), 4)


def _bucketize(
    rows: list[dict], key: str,
) -> list[dict[str, Any]]:
    """Group closed trades by an attribute and report count, mean r,
    and win rate per group. Skips trades where the key is missing
    or empty. Sorted by sample count descending."""
    buckets: dict[str, list[float]] = {}
    for r in rows:
        v = (r.get(key) or "").strip().lower() or "unknown"
        buckets.setdefault(v, []).append(float(r.get("r_multiple") or 0))
    out = [
        {
            key: name,
            "count": len(rs),
            "avg_r": _safe_mean(rs),
            "win_rate": _win_rate(rs),
        }
        for name, rs in buckets.items()
    ]
    out.sort(key=lambda x: -x["count"])
    return out


def _tp_tightening_grid(rows: list[dict]) -> list[dict[str, Any]]:
    """Project expectancy under a tighter TP. For trades that
    actually hit TP (close_reason='take_profit') a tighter TP at
    fraction f would have closed at f × realized_r. Other trades
    are unaffected (they exited before or below the original TP)."""
    out = []
    n = len(rows)
    for f in _TIGHTEN_GRID:
        adjusted = []
        for r in rows:
            base_r = float(r.get("r_multiple") or 0)
            if (r.get("close_reason") or "").lower() == "take_profit":
                adjusted.append(f * base_r)
            else:
                adjusted.append(base_r)
        out.append({
            "factor": f,
            "projected_expectancy_r": _safe_mean(adjusted),
            "applied_to": sum(
                1 for r in rows
                if (r.get("close_reason") or "").lower() == "take_profit"
            ),
            "of_total": n,
        })
    return out


def _sl_tightening_grid(rows: list[dict]) -> list[dict[str, Any]]:
    """Project expectancy under a tighter SL. For trades that
    actually hit SL (close_reason='stop_loss') a tighter SL at
    fraction f would have closed at -f×R (the SL is hit earlier
    along the same adverse path). Other trades unaffected."""
    out = []
    n = len(rows)
    for f in _TIGHTEN_GRID:
        adjusted = []
        for r in rows:
            base_r = float(r.get("r_multiple") or 0)
            if (r.get("close_reason") or "").lower() == "stop_loss":
                # base_r should be ≈ -1; tighter at fraction f → -f
                adjusted.append(-f)
            else:
                adjusted.append(base_r)
        out.append({
            "factor": f,
            "projected_expectancy_r": _safe_mean(adjusted),
            "applied_to": sum(
                1 for r in rows
                if (r.get("close_reason") or "").lower() == "stop_loss"
            ),
            "of_total": n,
        })
    return out


def _headline(
    expectancy: float,
    by_close_reason: list[dict[str, Any]],
    tp_grid: list[dict[str, Any]],
    sl_grid: list[dict[str, Any]],
) -> str:
    """Single-sentence guidance picked from the strongest signal."""
    if expectancy > 0.20:
        return f"Strategy is positive-expectancy at {expectancy:+.2f}R per trade. Don't change SL/TP without strong reason."
    if expectancy < -0.10:
        return f"Strategy is currently losing money at {expectancy:+.2f}R per trade. Pause before tuning SL/TP — fix entry signal first."

    # Find the tighter-SL factor that beats baseline by the most
    best_sl = max(sl_grid, key=lambda x: x["projected_expectancy_r"], default=None)
    if best_sl and best_sl["factor"] < 1.0 and best_sl["projected_expectancy_r"] > expectancy + 0.05:
        return (
            f"Data suggests tightening SL to {int(best_sl['factor']*100)}% of current would lift "
            f"expectancy from {expectancy:+.2f}R to {best_sl['projected_expectancy_r']:+.2f}R."
        )
    return f"Strategy is near break-even ({expectancy:+.2f}R). SL/TP grid shows no clear winner — collect more data."


async def compute_sltp_expectancy(db: Any, *, window_days: int = 30) -> dict[str, Any]:
    """Main entry point. ``db`` is the Motor database handle.

    Looks at closed crypto paper trades within the last
    ``window_days`` days. Returns the analytics blob defined in
    the module docstring.

    Never raises — Mongo failures resolve to an empty payload so
    the AdminPanel tile can render a "no data" placeholder.
    """
    if db is None:
        return _empty(window_days, "db_unavailable")

    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    flt = {
        "status": "closed",
        "closed_at": {"$gte": cutoff},
        "r_multiple": {"$ne": None},
    }
    try:
        cursor = db["crypto_paper_trades"].find(
            flt,
            {
                "_id": 0,
                "r_multiple": 1,
                "close_reason": 1,
                "direction": 1,
                "regime": 1,
            },
        )
        rows = [doc async for doc in cursor]
    except Exception:  # noqa: BLE001
        return _empty(window_days, "query_failed")

    if len(rows) < _MIN_SAMPLES:
        return _empty(window_days, "insufficient_data", available=len(rows))

    rs = [float(r.get("r_multiple") or 0) for r in rows]
    expectancy = _safe_mean(rs)
    win_rate = _win_rate(rs)
    by_reason = _bucketize(rows, "close_reason")
    by_dir = _bucketize(rows, "direction")
    by_regime = _bucketize(rows, "regime")
    tp_grid = _tp_tightening_grid(rows)
    sl_grid = _sl_tightening_grid(rows)
    return {
        "window_days": window_days,
        "closed_trades": len(rows),
        "expectancy_r": expectancy,
        "win_rate": win_rate,
        "by_close_reason": by_reason,
        "by_direction": by_dir,
        "by_regime": by_regime,
        "tp_tightening": tp_grid,
        "sl_tightening": sl_grid,
        "headline": _headline(expectancy, by_reason, tp_grid, sl_grid),
        "min_samples": _MIN_SAMPLES,
        "status": "ok",
    }


def _empty(window_days: int, reason: str, *, available: int = 0) -> dict[str, Any]:
    return {
        "window_days": window_days,
        "closed_trades": available,
        "expectancy_r": 0.0,
        "win_rate": 0.0,
        "by_close_reason": [],
        "by_direction": [],
        "by_regime": [],
        "tp_tightening": [],
        "sl_tightening": [],
        "headline": (
            f"Need at least {_MIN_SAMPLES} closed trades in the window to surface guidance "
            f"(currently {available})." if reason == "insufficient_data"
            else f"Analytics unavailable: {reason}."
        ),
        "min_samples": _MIN_SAMPLES,
        "status": reason,
    }
