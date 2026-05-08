"""News ingestion admin surface — Benzinga / Alpha Vantage / NEWS_SHOCK.

Extracted from ``routes/admin.py`` (~3272 lines, on the size allowlist)
to keep the admin surface domain-modular. URLs unchanged so no
frontend or test edits are needed:

  Benzinga slot:
    * ``GET  /api/admin/benzinga/status``
    * ``POST /api/admin/benzinga/smoke``
    * ``POST /api/admin/benzinga/news-telemetry/{symbol}``
    * ``POST /api/admin/benzinga/news-telemetry-batch``

  Alpha Vantage sentiment slot:
    * ``GET  /api/admin/av-news/status``
    * ``POST /api/admin/av-news/sentiment-telemetry/{symbol}``
    * ``POST /api/admin/av-news/sentiment-telemetry-batch``

  News-feeders scheduler:
    * ``POST /api/admin/news-feeders/tick``

  NEWS_SHOCK / catalyst readiness:
    * ``GET  /api/admin/news-shock/status``
    * ``POST /api/admin/news-shock/ensure-indexes``
    * ``GET  /api/admin/news-shock/burn-in``
    * ``GET  /api/admin/news-shock/ingestion-sparkline``

Owner-gated. Same module pattern as ``admin_conviction.py`` /
``admin_data_integrity.py`` / ``admin_adaptations.py`` —
duplicated ``_require_owner`` so this module has no inbound
dependency on ``admin.py``.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request


router = APIRouter(prefix="/api/admin", tags=["admin-news"])
logger = logging.getLogger(__name__)

db = None  # noqa: E305 — module-level handle, set by route_registry.wire_db


def set_db(database) -> None:
    global db
    db = database


async def _require_owner(request: Request):
    from routes.auth import get_current_user
    user = await get_current_user(request)
    if user.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Owner access required")
    return user


# ── Benzinga News API — slot health & smoke test ────────────────


@router.get("/benzinga/status")
async def benzinga_status(request: Request):
    """Return Benzinga integration health: key presence, daily quota
    usage, configured ceiling + interval.

    Does NOT make an outbound Benzinga call — pure DB + env read. A
    separate `/benzinga/smoke` endpoint triggers a real fetch.
    """
    await _require_owner(request)
    import os
    from services.benzinga_news_service import (
        _daily_call_count, _daily_ceiling,
        _min_interval_seconds, _today_key,
    )

    key = (os.environ.get("BENZINGA_API_KEY") or "").strip()
    key_configured = bool(key)
    ceiling = _daily_ceiling()
    used = await _daily_call_count(db)

    return {
        "key_configured": key_configured,
        "key_fingerprint": (key[:4] + "…" + key[-4:]) if len(key) >= 8 else None,
        "daily_ceiling": ceiling,
        "daily_calls_used": used,
        "daily_remaining": max(0, ceiling - used) if ceiling > 0 else None,
        "min_interval_seconds": _min_interval_seconds(),
        "today_utc": _today_key(),
    }


@router.post("/benzinga/smoke")
async def benzinga_smoke_test(request: Request, symbol: str = "AAPL"):
    """Trigger a single Benzinga News fetch to verify the slot is wired
    end-to-end. Consumes 1 call from the daily ceiling.

    Returns the summarized article count + channels for the symbol
    plus the raw meta block from the client (auth status, rate-limit
    headers, error_code if any). The most recent 5 sample titles are
    included for an at-a-glance operator read.
    """
    await _require_owner(request)
    from services.benzinga_news_service import fetch_news, summarize_articles

    result = await fetch_news(
        db=db,
        tickers=[symbol.upper()],
        minutes=240,
        page_size=25,
        display_output="headline",
    )
    summary = summarize_articles(result.get("articles") or [])

    return {
        "symbol": symbol.upper(),
        "summary": summary,
        "meta": result.get("meta", {}),
    }


@router.post("/benzinga/news-telemetry/{symbol}")
async def benzinga_news_telemetry_feed(request: Request, symbol: str):
    """Pull Benzinga news for a symbol and record the article count
    into the ``equity_telemetry`` rolling buffer.

    This is the NEWS_SHOCK feeder endpoint — each call contributes one
    sample to the baseline that Patent M's classifier reads. Consumes
    1 call from the daily ceiling.

    Returns the recorded sample + meta block from the upstream fetch.
    Safe to call from a scheduler; caller is responsible for cadence
    and ticker selection.
    """
    await _require_owner(request)
    from services.news_shock_feeder import fetch_and_record_news_telemetry

    return await fetch_and_record_news_telemetry(db, symbol)


@router.post("/benzinga/news-telemetry-batch")
async def benzinga_news_telemetry_batch(
    request: Request,
    symbols: str,
):
    """Feed a comma-separated batch of symbols (e.g. ``?symbols=AAPL,NVDA,MSFT``).
    Serialized by the 2s-spacing internal rate limiter. Short-circuits
    when the daily ceiling trips.

    Limit: 20 symbols per call to keep request wall-time bounded
    (~40s worst case at a 2s interval)."""
    await _require_owner(request)
    from services.news_shock_feeder import batch_feed_symbols

    syms = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if not syms:
        return {"error": "no_symbols_provided", "fed": 0, "skipped": 0}
    if len(syms) > 20:
        return {
            "error": "too_many_symbols",
            "max_per_call": 20,
            "requested": len(syms),
        }
    return await batch_feed_symbols(db, syms)


# ── Alpha Vantage sentiment feeder (NEWS_SHOCK sentiment leg) ──


@router.get("/av-news/status")
async def av_news_status(request: Request):
    """Return AV sentiment feeder health: key presence, daily quota
    usage, configured ceiling + interval."""
    await _require_owner(request)
    from services.av_sentiment_feeder import (
        _api_key, _daily_call_count, _daily_ceiling,
        _min_interval_seconds, _today_key,
    )

    key = _api_key()
    ceiling = _daily_ceiling()
    used = await _daily_call_count(db)
    return {
        "key_configured": bool(key),
        "key_fingerprint": (key[:4] + "…" + key[-4:]) if len(key) >= 8 else None,
        "daily_ceiling": ceiling,
        "daily_calls_used": used,
        "daily_remaining": max(0, ceiling - used) if ceiling > 0 else None,
        "min_interval_seconds": _min_interval_seconds(),
        "today_utc": _today_key(),
    }


@router.post("/av-news/sentiment-telemetry/{symbol}")
async def av_news_sentiment_feed(request: Request, symbol: str):
    """Pull AV NEWS_SENTIMENT for a symbol, compute sentiment magnitude,
    record into ``equity_telemetry`` rolling buffer. Consumes 1 call
    from the AV daily ceiling."""
    await _require_owner(request)
    from services.av_sentiment_feeder import fetch_and_record_sentiment

    return await fetch_and_record_sentiment(db, symbol)


@router.post("/av-news/sentiment-telemetry-batch")
async def av_news_sentiment_batch(request: Request, symbols: str):
    """Batch sentiment feeder. 20-symbol cap; short-circuits on AV
    rate-limit or daily-ceiling hit."""
    await _require_owner(request)
    from services.av_sentiment_feeder import batch_feed_sentiment

    syms = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if not syms:
        return {"error": "no_symbols_provided", "fed": 0, "skipped": 0}
    if len(syms) > 20:
        return {
            "error": "too_many_symbols",
            "max_per_call": 20,
            "requested": len(syms),
        }
    return await batch_feed_sentiment(db, syms)


@router.post("/news-feeders/tick")
async def news_feeders_manual_tick(request: Request):
    """Manually trigger one 15-min news-feeders tick. Useful for
    smoke-testing the scheduler without waiting for the next cron
    firing. Respects the market-hours gate; returns the skipped
    summary outside RTH."""
    await _require_owner(request)
    from services.news_feeders_scheduler import run_news_feeders_tick

    return await run_news_feeders_tick(db)


# ── Phase C — NEWS_SHOCK / catalyst readiness ──


@router.get("/news-shock/status")
async def news_shock_status(request: Request):
    """Return the ``catalyst_snapshots`` state — per-symbol shock
    state, z-score readiness, event risk, and the most recent headline.

    Read-only, cheap (single collection scan capped at 300 rows).
    Used by the admin dashboard + operator tooling to watch the
    Mon-AM baseline accumulation after a fresh deploy."""
    await _require_owner(request)

    now = datetime.now(timezone.utc)
    cursor = db.catalyst_snapshots.find({}, {"_id": 0}).limit(300)
    rows = await cursor.to_list(length=300)

    ready_count = 0
    elevated_count = 0
    high_count = 0
    symbols = []
    for row in rows:
        shock = row.get("news_shock", {}) or {}
        ready = bool(shock.get("zscore_ready"))
        if ready:
            ready_count += 1
        if shock.get("shock_state") == "elevated":
            elevated_count += 1
        if shock.get("shock_state") == "high":
            high_count += 1
        updated = row.get("updated_at")
        if isinstance(updated, datetime):
            updated = updated.isoformat()
        symbols.append({
            "symbol": row.get("symbol"),
            "zscore_ready": ready,
            "baseline_samples": shock.get("baseline_samples", 0),
            "news_zscore": shock.get("news_zscore"),
            "shock_state": shock.get("shock_state"),
            "sentiment_label": shock.get("sentiment_label"),
            "event_risk": row.get("event_risk"),
            "latest_headline": shock.get("latest_headline"),
            "updated_at": updated,
        })

    return {
        "ready_symbols": ready_count,
        "tracked_symbols": len(rows),
        "elevated_count": elevated_count,
        "high_count": high_count,
        "symbols": symbols,
        "updated_at": now.isoformat(),
    }


@router.post("/news-shock/ensure-indexes")
async def news_shock_ensure_indexes(request: Request):
    """One-shot helper to create the three Mongo indexes the NEWS_SHOCK
    layer depends on. Idempotent; safe to re-run. Kept as an endpoint
    (rather than eager startup hook) so the operator can time the
    first creation with an empty database."""
    await _require_owner(request)
    created = []
    try:
        await db.catalyst_events.create_index(
            [("symbol", 1), ("event_time", -1)],
        )
        created.append("catalyst_events.symbol_event_time")
    except Exception as exc:  # noqa: BLE001
        created.append(f"catalyst_events_error:{exc}")
    try:
        await db.catalyst_events.create_index("event_id", unique=True)
        created.append("catalyst_events.event_id_unique")
    except Exception as exc:  # noqa: BLE001
        created.append(f"catalyst_events_event_id_error:{exc}")
    try:
        await db.news_telemetry.create_index(
            [("symbol", 1), ("created_at", -1)],
        )
        created.append("news_telemetry.symbol_created_at")
    except Exception as exc:  # noqa: BLE001
        created.append(f"news_telemetry_error:{exc}")
    try:
        await db.catalyst_snapshots.create_index("symbol", unique=True)
        created.append("catalyst_snapshots.symbol_unique")
    except Exception as exc:  # noqa: BLE001
        created.append(f"catalyst_snapshots_error:{exc}")
    return {"created": created}


@router.get("/news-shock/burn-in")
async def news_shock_burn_in(request: Request):
    """One-shot Monday burn-in health check.

    Aggregates the four independent health signals the operator
    should watch on first-market-open after a fresh deploy:

    1. ``scheduler_state.news_feeders_rotation`` — is the tick
       running? Last ``updated_at`` + current offset.
    2. ``catalyst_events`` — is the feeder persisting articles?
       Row count + most recent event_time.
    3. ``news_telemetry`` — is the shock-compute step recording
       baselines? Row count + most recent created_at.
    4. ``catalyst_snapshots`` — is the projection landing?
       Total tracked + ``zscore_ready`` count + top-3 most
       recently updated.
    5. ``decision_proof_chain`` — are ``SMART_MONEY_VERIFIED``
       blocks appearing on real equity decisions?
    6. ``equity_telemetry_baselines`` — is ``_warm_one`` feeding
       atr/volume/dollar_volume? Samples per symbol histogram.

    Cheap single read per collection — safe to poll every 30 s
    during the burn-in window.
    """
    await _require_owner(request)

    def _iso(dt):
        if isinstance(dt, datetime):
            return dt.replace(tzinfo=dt.tzinfo or timezone.utc).isoformat()
        return dt

    # 1. Scheduler state
    rot_doc = await db.scheduler_state.find_one(
        {"_id": "news_feeders_rotation"}, {"_id": 0},
    )

    # 2. Catalyst events
    catalyst_events_total = await db.catalyst_events.count_documents({})
    latest_catalyst_event = await db.catalyst_events.find_one(
        {}, {"_id": 0, "event_time": 1, "symbol": 1, "source": 1, "headline": 1},
        sort=[("event_time", -1)],
    ) or {}

    # 3. News telemetry rows
    news_tel_total = await db.news_telemetry.count_documents({})
    latest_news_tel = await db.news_telemetry.find_one(
        {}, {"_id": 0, "created_at": 1, "symbol": 1, "news_volume": 1},
        sort=[("created_at", -1)],
    ) or {}

    # 4. Catalyst snapshots
    snapshots_total = await db.catalyst_snapshots.count_documents({})
    ready_total = await db.catalyst_snapshots.count_documents(
        {"news_shock.zscore_ready": True},
    )
    top_cursor = db.catalyst_snapshots.find(
        {}, {"_id": 0, "symbol": 1, "event_risk": 1, "updated_at": 1, "news_shock.shock_state": 1},
    ).sort("updated_at", -1).limit(3)
    top_snapshots = await top_cursor.to_list(length=3)
    for s in top_snapshots:
        s["updated_at"] = _iso(s.get("updated_at"))

    # 5. Smart Money proof-chain blocks (last 24 h)
    since_24h = datetime.now(timezone.utc) - timedelta(hours=24)
    smart_money_blocks_24h = await db.decision_proof_chain.count_documents({
        "event_type": "SMART_MONEY_VERIFIED",
        "created_at": {"$gte": since_24h},
    })

    # 6. Equity telemetry baselines (atr/volume/dollar_volume)
    baseline_total = await db.equity_telemetry_baselines.count_documents({})
    baseline_cursor = db.equity_telemetry_baselines.find(
        {}, {"_id": 0, "symbol": 1, "samples": 1},
    ).limit(5)
    baseline_samples = []
    async for row in baseline_cursor:
        samples = row.get("samples", []) or []
        last = samples[-1] if samples else {}
        baseline_samples.append({
            "symbol": row.get("symbol"),
            "sample_count": len(samples),
            "has_dollar_volume": "dollar_volume" in last,
            "has_news_count": "news_count" in last,
            "has_news_sentiment": "news_sentiment_abs" in last,
            "latest_at": _iso(last.get("at")) if isinstance(last.get("at"), datetime) else last.get("at"),
        })

    return {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "scheduler": {
            "last_offset": (rot_doc or {}).get("offset"),
            "last_updated_at": _iso((rot_doc or {}).get("updated_at")),
        },
        "catalyst_events": {
            "total": catalyst_events_total,
            "latest_event_time": _iso(latest_catalyst_event.get("event_time")),
            "latest_symbol": latest_catalyst_event.get("symbol"),
            "latest_source": latest_catalyst_event.get("source"),
            "latest_headline": latest_catalyst_event.get("headline"),
        },
        "news_telemetry": {
            "total_rows": news_tel_total,
            "latest_created_at": _iso(latest_news_tel.get("created_at")),
            "latest_symbol": latest_news_tel.get("symbol"),
            "latest_news_volume": latest_news_tel.get("news_volume"),
        },
        "catalyst_snapshots": {
            "total": snapshots_total,
            "zscore_ready": ready_total,
            "most_recent": top_snapshots,
        },
        "smart_money_blocks_24h": smart_money_blocks_24h,
        "equity_telemetry": {
            "total_symbols_tracked": baseline_total,
            "sample": baseline_samples,
        },
    }


@router.get("/news-shock/ingestion-sparkline")
async def news_shock_ingestion_sparkline(request: Request, hours: int = 24):
    """Hourly Benzinga + Alpha Vantage article ingestion counts over a
    rolling N-hour window for the burn-in sparkline.

    Returns
    -------
    {
        "hours": int,
        "buckets": ["2026-05-03T01:00:00Z", ...],   # ascending UTC hour starts
        "benzinga": [int, int, ...],                # per-bucket counts
        "alpha_vantage": [int, int, ...],
        "totals": {"benzinga": int, "alpha_vantage": int},
        "current_hour": {"benzinga": int, "alpha_vantage": int},
    }

    Aggregates ``catalyst_events`` by hour bucket. Caps at 168 hours
    (one week) to keep the aggregation cheap and the sparkline
    legible.
    """
    await _require_owner(request)

    hours = max(1, min(int(hours if hours is not None else 24), 168))
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    start = now - timedelta(hours=hours - 1)

    # One aggregation pass — group by (source, hour-bucket) and emit
    # the per-source per-hour count. We fan it out into two parallel
    # arrays in Python because the bucket list is short.
    pipeline = [
        {"$match": {"event_time": {"$gte": start}}},
        {"$group": {
            "_id": {
                "source": "$source",
                "bucket": {
                    "$dateTrunc": {
                        "date": "$event_time",
                        "unit": "hour",
                        "timezone": "UTC",
                    },
                },
            },
            "count": {"$sum": 1},
        }},
    ]
    rows = await db.catalyst_events.aggregate(pipeline).to_list(length=None)

    bucket_starts: list[datetime] = []
    for i in range(hours):
        bucket_starts.append(start + timedelta(hours=i))

    benzinga = [0] * hours
    av = [0] * hours
    for r in rows:
        src = (r.get("_id", {}) or {}).get("source")
        bucket = (r.get("_id", {}) or {}).get("bucket")
        if not isinstance(bucket, datetime):
            continue
        if bucket.tzinfo is None:
            bucket = bucket.replace(tzinfo=timezone.utc)
        # Find bucket index by hour delta.
        delta_h = int((bucket - start).total_seconds() // 3600)
        if delta_h < 0 or delta_h >= hours:
            continue
        if src == "benzinga":
            benzinga[delta_h] = int(r.get("count", 0))
        elif src == "alpha_vantage":
            av[delta_h] = int(r.get("count", 0))

    return {
        "hours": hours,
        "buckets": [b.isoformat() for b in bucket_starts],
        "benzinga": benzinga,
        "alpha_vantage": av,
        "totals": {
            "benzinga": sum(benzinga),
            "alpha_vantage": sum(av),
        },
        "current_hour": {
            "benzinga": benzinga[-1] if benzinga else 0,
            "alpha_vantage": av[-1] if av else 0,
        },
    }
