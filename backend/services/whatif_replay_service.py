"""What-If replay service.

Two operations:

* :func:`backfill_outcomes` — one-time idempotent migration that
  publishes every closed ``paper_trades`` row and every verified
  ``predictions`` row into the immutable PRD outcome ledger via the
  firewall. Idempotent because :func:`firewall.publish_resolved` is
  uniqueness-keyed on ``outcome_id``.

* :func:`whatif_projection` — given a time window, projects each
  registered engine's schema against the outcomes in the window
  and returns a side-by-side stats dict. Engines do NOT mutate
  their persistent state during projection — projection is pure
  in-memory aggregation.

PRD-tagged: reads only from PRD collections (the firewall ledger).
The backfill helper is an exception — it has to read DTD collections
to seed the ledger. That read is permitted under the dual-stack
spec because crossing happens via the firewall: DTD data → firewall
write → PRD data. The backfill is the operator-managed equivalent
of the runtime DTD-side ``firewall.publish_resolved`` call that
``paper_trade_closer`` and ``prediction_tracker`` will eventually
make on every settlement.
"""
from __future__ import annotations

__domain__ = "PRD"

import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Optional

from services.ai_core_engine import (
    LearningEngine, LearningEngineRegistry, registry,
    _normalise_trade,
)
from services.firewall import publish_resolved, read_resolved
from services.prediction_tracker import normalize_confidence

logger = logging.getLogger(__name__)


# ── Backfill ─────────────────────────────────────────────────────────


def _classify_asset(symbol: str) -> str:
    s = (symbol or "").upper()
    if "/" in s or (s.endswith("USD") and len(s) <= 8):
        return "crypto"
    return "equity"


def _to_iso(ts: Any) -> Optional[str]:
    if ts is None:
        return None
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts.isoformat()
    return str(ts)


async def backfill_outcomes(db: Any, *, limit: int = 5000) -> dict:
    """Publish closed paper_trades + verified predictions into the
    firewall ledger. Idempotent — re-runs are no-ops on already-
    published outcomes thanks to the unique index on ``outcome_id``.

    Settle window is set to 0 here because the source rows are
    already settled by the closer/labeler. The window matters only
    for forward-time runtime calls.
    """
    if db is None:
        return {"ok": False, "reason": "db_unavailable"}

    paper_inserted = 0
    paper_deduped = 0
    pred_inserted = 0
    pred_deduped = 0
    skipped = 0

    # ── paper_trades ──
    cursor = db["paper_trades"].find(
        {"status": "closed", "outcome": {"$in": ["win", "loss", "flat"]}},
        {"_id": 0},
    ).limit(limit)
    async for t in cursor:
        symbol = (t.get("ticker") or "").upper()
        trade_id = t.get("trade_id")
        if not symbol or not trade_id:
            skipped += 1
            continue
        payload = {
            "outcome_id": f"paper_trades:{trade_id}",
            "source": "paper_trades",
            "symbol": symbol,
            "outcome": t.get("outcome"),
            "resolved_at": _to_iso(t.get("closed_at")) or _to_iso(t.get("created_at")),
            "direction": t.get("direction"),
            "confidence": normalize_confidence(t.get("confidence")),
            "regime": t.get("regime"),
            "agent": "paper_trader",
            "asset_type": _classify_asset(symbol),
            "entry_price": t.get("entry_price"),
            "exit_price": t.get("exit_price"),
            "pnl_usd": t.get("pnl_usd"),
            "pnl_pct": t.get("pnl_pct"),
        }
        res = await publish_resolved(payload, settle_seconds=0)
        if not res.get("ok"):
            skipped += 1
            continue
        if res.get("deduped"):
            paper_deduped += 1
        else:
            paper_inserted += 1

    # ── predictions ──
    cursor = db["predictions"].find(
        {
            "verified_24h": {"$ne": None},
            "verified_24h.correct": {"$in": [True, False]},
        },
        {"_id": 0},
    ).limit(limit)
    async for p in cursor:
        v24 = p.get("verified_24h") or {}
        prediction_id = p.get("prediction_id")
        symbol = (p.get("symbol") or "").upper()
        if not prediction_id or not symbol:
            skipped += 1
            continue
        outcome = "win" if v24.get("correct") is True else "loss"
        payload = {
            "outcome_id": f"predictions:{prediction_id}",
            "source": "predictions",
            "symbol": symbol,
            "outcome": outcome,
            "resolved_at": v24.get("verified_at"),
            "direction": p.get("direction"),
            "confidence": normalize_confidence(p.get("confidence")),
            "agent": p.get("feature") or "unknown",
            "asset_type": _classify_asset(symbol),
            "entry_price": p.get("price_at_prediction"),
            "exit_price": v24.get("price"),
        }
        res = await publish_resolved(payload, settle_seconds=0)
        if not res.get("ok"):
            skipped += 1
            continue
        if res.get("deduped"):
            pred_deduped += 1
        else:
            pred_inserted += 1

    return {
        "ok": True,
        "paper_trades": {"inserted": paper_inserted, "deduped": paper_deduped},
        "predictions": {"inserted": pred_inserted, "deduped": pred_deduped},
        "skipped": skipped,
    }


# ── What-If projection ───────────────────────────────────────────────


def _project_engine_in_memory(
    engine: LearningEngine,
    outcomes: list[dict],
) -> dict:
    """Run a *transient* engine of the same schema over the provided
    outcomes and return its stats snapshot. Does NOT mutate the
    real engine's persistent state."""
    transient = LearningEngine(name=f"{engine.name}__transient", schema=engine.schema)
    for o in outcomes:
        # Outcomes from the ledger have the firewall envelope; map to
        # the engine's record_trade input shape.
        raw = {
            "source": o.get("source"),
            "source_id": o.get("outcome_id", "").split(":", 1)[-1] or "x",
            "symbol": o.get("symbol"),
            "direction": o.get("direction"),
            "confidence": o.get("confidence"),
            "regime": o.get("regime"),
            "agent": o.get("agent"),
            "asset_type": o.get("asset_type"),
            "entry_price": o.get("entry_price"),
            "exit_price": o.get("exit_price"),
            "outcome": o.get("outcome"),
            "recorded_at": o.get("resolved_at"),
        }
        normalised = _normalise_trade(raw)
        if normalised is None:
            continue
        if normalised["trade_key"] in transient._seen_keys:
            continue
        transient._apply_in_memory(normalised, hydrate=False)
    return {
        "stats": transient.stats_snapshot(),
        "conditions": transient.conditions_snapshot(),
    }


def _bucket_lift(conditions: dict, dimension: str, min_total: int = 30) -> Optional[float]:
    rows = conditions.get(dimension, [])
    rows = [r for r in rows if r["total"] >= min_total and r["win_rate"] is not None]
    if len(rows) < 2:
        return None
    wrs = [r["win_rate"] for r in rows]
    return round(max(wrs) - min(wrs), 4)


async def whatif_projection(
    *,
    since: Optional[str] = None,
    until: Optional[str] = None,
    engines: Optional[list[str]] = None,
    sources: Optional[list[str]] = None,
    dimension: str = "agent",
    min_total: int = 30,
    limit: int = 5000,
) -> dict:
    """Project each requested engine's schema against the resolved
    outcomes in the window. Read-only.

    Returns ``{window, engines: [{name, schema_name, stats,
    bucket_lift, top_buckets, bottom_buckets}], delta}``.
    """
    rows = await read_resolved(since=since, sources=sources, limit=limit)
    if until:
        rows = [r for r in rows if (r.get("resolved_at") or "") <= until]

    target_names = engines if engines else registry.names()
    out_engines = []
    for name in target_names:
        engine = registry.get(name)
        if engine is None:
            continue
        proj = _project_engine_in_memory(engine, rows)
        lift = _bucket_lift(proj["conditions"], dimension, min_total)
        # Top/bottom 3 buckets in the chosen dimension for the UI
        dim_rows = proj["conditions"].get(dimension, [])
        top = dim_rows[:3]
        bottom = dim_rows[-3:] if len(dim_rows) > 3 else []
        out_engines.append({
            "name": name,
            "schema_name": engine.schema.name,
            "is_live": name == registry.live_name,
            "stats": proj["stats"],
            "bucket_lift": lift,
            "top_buckets": top,
            "bottom_buckets": bottom,
        })

    # Compute live-vs-each-candidate win-rate delta for the headline.
    delta = {}
    live_row = next((e for e in out_engines if e["is_live"]), None)
    live_wr = live_row["stats"].get("win_rate") if live_row else None
    if live_wr is not None:
        for e in out_engines:
            if e["is_live"]:
                continue
            cand_wr = e["stats"].get("win_rate")
            if cand_wr is None:
                continue
            delta[e["name"]] = round(cand_wr - live_wr, 4)

    return {
        "window": {"since": since, "until": until, "outcomes_in_corpus": len(rows)},
        "dimension": dimension,
        "min_total": min_total,
        "live": registry.live_name,
        "engines": out_engines,
        "winrate_delta_vs_live": delta,
    }
