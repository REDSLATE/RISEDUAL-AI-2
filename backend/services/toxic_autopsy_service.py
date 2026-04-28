"""Toxic Spike Autopsy — aggregates high-confidence failed predictions.

Answers *why* the nightly cleanup flagged 56 toxic spikes, not just
*that* it did. Groups by failure_code, feature (agent origin),
confidence bucket, direction, sector, and model version, so the
operator can tell whether the problem is "tech fakeouts on NVDA"
vs "REGIME_SHIFT on the signal_dispatcher in a specific model rev".

Source of truth: MongoDB `predictions` collection. A prediction is
"toxic" when:
  - verified_24h exists and verified_24h.correct is False
    (so NEUTRAL grades are skipped — the same rule the nightly
    cleanup uses), AND
  - normalize_confidence(confidence) * 100 > min_confidence_pct, AND
  - verified_24h.verified_at is within the lookback window.

Read-only; safe to call from admin endpoints. Never mutates data.
"""
from __future__ import annotations

import logging
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from typing import Any

from services.prediction_tracker import normalize_confidence
from services.symbol_sector_resolver import resolve_sectors_bulk

logger = logging.getLogger(__name__)

_SAMPLE_CAP = 200
_TOP_N = 15

# Confidence buckets expressed on the 0-100 scale.
_BUCKETS: list[tuple[str, float, float]] = [
    ("80-85", 80.0, 85.0),
    ("85-90", 85.0, 90.0),
    ("90-95", 90.0, 95.0),
    ("95-100", 95.0, 100.01),  # inclusive upper
]


def _bucket_for(conf_pct: float) -> str:
    for label, lo, hi in _BUCKETS:
        if lo <= conf_pct < hi:
            return label
    return "other"


def _direction_family(direction: str) -> str:
    d = (direction or "").upper()
    if d in {"BUY", "BULLISH", "LONG", "UP", "STRONG_BUY", "WEAK_BUY"}:
        return "BULLISH"
    if d in {"SELL", "BEARISH", "SHORT", "DOWN", "STRONG_SELL", "WEAK_SELL"}:
        return "BEARISH"
    if d in {"HOLD", "NEUTRAL", "WAIT"}:
        return "NEUTRAL"
    return d or "UNKNOWN"


def _grouped(rows: list[dict], key_fn) -> list[dict]:
    """Generic group-and-count that also tracks avg confidence per key."""
    counts: Counter[str] = Counter()
    conf_sums: defaultdict[str, float] = defaultdict(float)
    for r in rows:
        k = key_fn(r) or "Unknown"
        counts[k] += 1
        conf_sums[k] += r["confidence_pct"]
    total = sum(counts.values()) or 1
    out = []
    for k, c in counts.most_common():
        out.append({
            "key": k,
            "count": c,
            "pct": round(c / total, 4),
            "avg_confidence_pct": round(conf_sums[k] / c, 2),
        })
    return out


def _top_offenders(rows: list[dict]) -> list[dict]:
    by_sym: defaultdict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_sym[r["symbol"]].append(r)
    ranked = []
    for sym, items in by_sym.items():
        grades: Counter[str] = Counter()
        failure_codes: Counter[str] = Counter()
        features: Counter[str] = Counter()
        for it in items:
            if it.get("grade"):
                grades[it["grade"]] += 1
            if it.get("failure_code"):
                failure_codes[it["failure_code"]] += 1
            if it.get("feature"):
                features[it["feature"]] += 1
        avg_conf = sum(i["confidence_pct"] for i in items) / max(1, len(items))
        ranked.append({
            "symbol": sym,
            "count": len(items),
            "avg_confidence_pct": round(avg_conf, 2),
            "sector": items[0].get("sector") or "Unknown",
            "grades": dict(grades),
            "failure_codes": dict(failure_codes),
            "features": dict(features),
        })
    ranked.sort(key=lambda r: (r["count"], r["avg_confidence_pct"]), reverse=True)
    return ranked[:_TOP_N]


def _sample(rows: list[dict]) -> list[dict]:
    # Keep the most recent N, strip internal-only fields.
    rows_sorted = sorted(
        rows, key=lambda r: r.get("verified_at") or "", reverse=True
    )
    samples = []
    for r in rows_sorted[:_SAMPLE_CAP]:
        samples.append({
            "prediction_id": r.get("prediction_id"),
            "symbol": r.get("symbol"),
            "feature": r.get("feature"),
            "direction": r.get("direction"),
            "confidence_pct": r.get("confidence_pct"),
            "timestamp": r.get("timestamp"),
            "verified_at": r.get("verified_at"),
            "grade": r.get("grade"),
            "failure_code": r.get("failure_code"),
            "failure_reason": r.get("failure_reason"),
            "sector": r.get("sector"),
            "model_version": r.get("model_version"),
            "price_at_prediction": r.get("price_at_prediction"),
            "price_verified": r.get("price_verified"),
        })
    return samples


async def build_autopsy(
    db: Any,
    *,
    days: int = 2,
    min_confidence_pct: float = 80.0,
) -> dict:
    """Main entry point — returns the grouped autopsy payload."""
    if db is None:
        return {
            "window": {"days": days},
            "min_confidence_pct": min_confidence_pct,
            "total_failures": 0,
            "error": "database_unavailable",
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    now = datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    since_iso = since.isoformat()

    # Pull verified-miss rows in the window. We filter confidence in
    # Python because the column is mixed-scale historically (0-1 vs
    # 0-100) and `normalize_confidence` is the single source of truth.
    cursor = db.predictions.find(
        {
            "verified_24h": {"$ne": None},
            "verified_24h.correct": False,
            "verified_24h.verified_at": {"$gte": since_iso},
        },
        {"_id": 0},
    )

    rows: list[dict] = []
    symbols: set[str] = set()
    total_misses_in_window = 0

    async for p in cursor:
        total_misses_in_window += 1
        # normalize_confidence already returns 0-100 scale — don't re-scale.
        conf_pct = round(normalize_confidence(p.get("confidence")), 2)
        if conf_pct <= min_confidence_pct:
            continue
        v24 = p.get("verified_24h") or {}
        symbol = (p.get("symbol") or "").upper()
        symbols.add(symbol)
        rows.append({
            "prediction_id": p.get("prediction_id"),
            "symbol": symbol,
            "feature": p.get("feature") or "unknown",
            "direction": _direction_family(p.get("direction") or ""),
            "confidence_pct": conf_pct,
            "timestamp": p.get("timestamp"),
            "verified_at": v24.get("verified_at"),
            "grade": v24.get("grade") or ("STRONG_MISS" if v24.get("correct") is False else None),
            "failure_code": v24.get("failure_code") or "UNKNOWN",
            "failure_reason": v24.get("failure_reason"),
            "model_version": p.get("model_version") or "unversioned",
            "price_at_prediction": p.get("price_at_prediction"),
            "price_verified": v24.get("price"),
        })

    # Resolve sectors in one batch.
    sectors = await resolve_sectors_bulk(db, list(symbols))
    for r in rows:
        r["sector"] = sectors.get(r["symbol"], "Unknown")

    avg_conf = (
        round(sum(r["confidence_pct"] for r in rows) / len(rows), 2)
        if rows else 0.0
    )

    return {
        "window": {
            "days": days,
            "since": since_iso,
            "until": now.isoformat(),
        },
        "min_confidence_pct": min_confidence_pct,
        "total_failures": len(rows),
        "total_misses_in_window": total_misses_in_window,
        "avg_confidence_pct": avg_conf,
        "unique_symbols": len(symbols),
        "by_failure_code": _grouped(rows, lambda r: r.get("failure_code")),
        "by_feature": _grouped(rows, lambda r: r.get("feature")),
        "by_confidence_bucket": _grouped(rows, lambda r: _bucket_for(r["confidence_pct"])),
        "by_direction": _grouped(rows, lambda r: r.get("direction")),
        "by_sector": _grouped(rows, lambda r: r.get("sector")),
        "by_model_version": _grouped(rows, lambda r: r.get("model_version")),
        "by_grade": _grouped(rows, lambda r: r.get("grade") or "STRONG_MISS"),
        "top_offenders": _top_offenders(rows),
        "samples": _sample(rows),
        "generated_at": now.isoformat(),
    }
