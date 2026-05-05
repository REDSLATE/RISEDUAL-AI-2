"""RISEDUAL AI — FastAPI Application Entry Point.

Thin orchestrator: connects MongoDB, registers route modules, handles startup/shutdown.
"""
from fastapi import FastAPI, APIRouter
from fastapi.responses import FileResponse
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
from pathlib import Path
from pydantic import BaseModel, Field, ConfigDict

import uuid
from datetime import datetime, timezone

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

from route_registry import register_all_routers, wire_db, seed_admin, create_indexes

# MongoDB connection
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

# FastAPI app
app = FastAPI()

# Minimal status-check router (kept in server.py for health checks)
api_router = APIRouter(prefix="/api")


class StatusCheck(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    client_name: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class StatusCheckCreate(BaseModel):
    client_name: str


@api_router.get("/")
async def root():
    return {"message": "RISEDUAL AI API - Ready"}


@api_router.get("/ready")
async def readiness_check():
    """Health/readiness check — confirms DB is connected and routes are registered."""
    try:
        await db.command("ping")
        db_status = "connected"
    except Exception:
        db_status = "disconnected"
    return {
        "status": "ok" if db_status == "connected" else "degraded",
        "db": db_status,
        "routes": len(app.routes),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@api_router.get("/health")
async def health_check():
    """Conventional health-check alias for ``/ready``.

    External monitors (uptime probes, status pages, the Emergent
    deploy liveness check, ad-hoc operator curl) reach for ``/health``
    by reflex. Pointing it at the same readiness body as ``/ready``
    means we have one source of truth without forcing every
    integration to learn our naming.
    """
    return await readiness_check()


@api_router.get("/download/codebase-pdf")
async def download_codebase_pdf():
    pdf_path = "/app/RISEDUAL_AI_Complete_Codebase.pdf"
    if os.path.exists(pdf_path):
        return FileResponse(
            pdf_path,
            media_type="application/pdf",
            filename="RISEDUAL_AI_Complete_Codebase.pdf"
        )
    return {"error": "PDF not found"}


@api_router.post("/status", response_model=StatusCheck)
async def create_status_check(input: StatusCheckCreate):
    status_obj = StatusCheck(**input.model_dump())
    doc = status_obj.model_dump()
    doc['timestamp'] = doc['timestamp'].isoformat()
    await db.status_checks.insert_one(doc)
    return status_obj


@api_router.get("/status", response_model=list[StatusCheck])
async def get_status_checks():
    status_checks = await db.status_checks.find({}, {"_id": 0}).to_list(1000)
    for check in status_checks:
        if isinstance(check['timestamp'], str):
            check['timestamp'] = datetime.fromisoformat(check['timestamp'])
    return status_checks


# Register all routers
app.include_router(api_router)
register_all_routers(app)

# CORS — dynamic origin reflection for httpOnly cookie auth.
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest

class DynamicCORSMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: StarletteRequest, call_next):
        origin = request.headers.get("origin", "")
        if request.method == "OPTIONS":
            from starlette.responses import Response as StarletteResponse
            resp = StarletteResponse(status_code=204)
            if origin:
                resp.headers["Access-Control-Allow-Origin"] = origin
                resp.headers["Access-Control-Allow-Credentials"] = "true"
            resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS, HEAD, PATCH"
            resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Requested-With, X-API-Key"
            resp.headers["Access-Control-Max-Age"] = "600"
            return resp
        response = await call_next(request)
        if origin:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Credentials"] = "true"
        return response

app.add_middleware(DynamicCORSMiddleware)

# Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# ── Global exception sanitizer ────────────────────────────────────────
# Many routes legitimately do `raise HTTPException(status_code=500,
# detail=str(e))` to surface failures. That leaks internal exception
# strings (Mongo errors, file paths, library tracebacks) to the client.
#
# Policy:
#   * 4xx — KEEP detail. These are intentional client-facing messages
#     (validation, auth, "not found"). Devs control them.
#   * 500 — REPLACE detail with a generic string. Full original detail
#     is logged server-side for debugging.
#   * 501/502/503/504 — KEEP detail. These are typically capability /
#     availability messages devs want users to see ("Tradier not
#     configured", "rate limited"). Routes opt into raw exposure here.
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


@app.exception_handler(StarletteHTTPException)
async def sanitize_http_exception(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 500:
        logger.error(
            f"[5xx-sanitized] path={request.url.path} "
            f"status=500 raw_detail={exc.detail!r}"
        )
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error"},
            headers=getattr(exc, "headers", None) or {},
        )
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
        headers=getattr(exc, "headers", None) or {},
    )


@app.exception_handler(Exception)
async def sanitize_unhandled_exception(request: Request, exc: Exception):
    """Catch-all for unhandled exceptions — never expose internals."""
    logger.exception(
        f"[unhandled] path={request.url.path} type={type(exc).__name__}"
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )


async def _pregen_watchlist_intel(database):
    """Pre-generate watchlist intelligence for all users with watchlists (runs 5:30 AM UTC)."""
    try:
        from services.watchlist_intelligence_service import generate_watchlist_summary
        api_key = os.environ.get("EMERGENT_LLM_KEY")
        count = 0
        cursor = database.watchlists.find({"tickers": {"$exists": True, "$ne": []}})
        async for wl_doc in cursor:
            user_id = wl_doc.get("user_id")
            tickers = wl_doc.get("tickers", [])
            if not tickers or not user_id:
                continue
            try:
                await generate_watchlist_summary(api_key, tickers, db=database, user_id=user_id)
                count += 1
            except Exception as e:
                logger.warning(f"Failed to pre-generate watchlist intel for {user_id}: {e}")
        logger.info(f"Watchlist intelligence pre-generation complete: {count} users processed")
    except Exception as e:
        logger.error(f"Watchlist pre-generation failed: {e}")


async def _run_memory_cleanup():
    """Scheduled nightly memory cleanup task."""
    try:
        from services.market_memory_service import nightly_cleanup
        result = await nightly_cleanup(days_to_keep=90, toxic_confidence_threshold=80.0)
        logger.info(f"Nightly memory cleanup: {result.get('toxic_removed', 0)} toxic + {result.get('obsolete_removed', 0)} obsolete removed")
    except Exception as e:
        logger.warning(f"Memory cleanup failed: {e}")
    # ── Backfill prediction_date on legacy rows ──
    # Idempotent + bounded; self-disables once the residual is zero.
    # Pinned to the same 2:00 UTC tick as the rest of nightly
    # maintenance so it lives or dies with the cleanup window.
    try:
        from services.prediction_date_backfill import backfill_prediction_date
        await backfill_prediction_date(db)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"prediction_date backfill failed: {e}")


async def _run_waitlist_auto_invite():
    """Scheduled daily task: Auto-invite top 5 users from the waitlist."""
    try:
        from services.waitlist_service import auto_invite_top_users
        invited = await auto_invite_top_users(batch_size=5)
        if invited:
            logger.info(f"Waitlist auto-invite: Sent {len(invited)} War Room invites")
            for u in invited:
                logger.info(f"  Invited: {u['email']} (rank #{u['rank']}, key: {u['beta_key']}, email_sent: {u['email_sent']})")
        else:
            logger.info("Waitlist auto-invite: No users to invite")
    except Exception as e:
        logger.warning(f"Waitlist auto-invite failed: {e}")


async def _run_research_shadow_scorer():
    """Deferred counterfactual scorer for research_shadow_decisions.

    Sweeps pending dissents whose asset-typed lookahead window has
    elapsed and patches their tactical_score. Tier-3 firewall:
    writes ONLY to research_shadow_decisions.
    """
    try:
        from services.research_shadow_scorer import run_scorer_pass
        result = await run_scorer_pass(db)
        if result.get("scored", 0) > 0:
            logger.info(
                "[shadow-scorer] tick: scanned=%s scored=%s skipped=%s",
                result.get("scanned"), result.get("scored"), result.get("skipped"),
            )
    except Exception as e:
        logger.warning(f"Research shadow scorer failed: {e}")


async def _run_patent_watch_refresh():
    """Daily USPTO refresh for every saved Patent Watch query.

    No-op when no queries are configured. Errors are absorbed —
    a flaky USPTO endpoint must never block the scheduler.
    """
    try:
        from services.patent_watch_service import refresh_all_queries
        result = await refresh_all_queries()
        if result.get("queries", 0) > 0:
            logger.info(
                "[patent-watch] daily: queries=%s fetched=%s errors=%s",
                result.get("queries"), result.get("fetched"), result.get("errors"),
            )
    except Exception as e:
        logger.warning(f"Patent watch refresh failed: {e}")


async def _run_ops_alerter_tick():
    """Wedge-detector — diffs the Health snapshot's notes against
    last run and webhooks on transitions. No-op when
    OPS_ALERT_WEBHOOK_URL is unset."""
    try:
        from services.ops_alerter import run_tick, is_configured
        if not is_configured():
            return
        result = await run_tick(db)
        if result.get("posted_alert") or result.get("posted_resolved"):
            logger.info(
                "[ops-alerter] tick: fresh=%s resolved=%s suppressed=%s",
                result.get("fresh_alerts"),
                result.get("resolved_alerts"),
                result.get("suppressed_dedup"),
            )
    except Exception as e:
        logger.warning(f"Ops alerter tick failed: {e}")


async def _run_ai_core_nightly():
    """AI Core nightly sweep — pulls newly resolved trades from
    `paper_trades` + verified `predictions` and emits a dedup-safe
    daily summary alert. Same-day re-runs are no-ops by design
    (alert id = ``nightly_sweep:YYYY-MM-DD`` with unique index)."""
    try:
        from services.ai_core_engine import learning_engine, registry
        from services.ai_core_autowire import autowire_sweep
        from services.ai_core_alerts import emit as emit_alert
        # Hydrate every engine so bridge-eligible compute below sees
        # the full Mongo history, not just whatever's been ingested
        # since boot.
        for engine in registry.all():
            await engine.hydrate()
        sweep = await autowire_sweep(db)
        snap = learning_engine.stats_snapshot()
        msg_parts = []
        if snap.get("total_resolved"):
            msg_parts.append(f"{snap['total_resolved']} resolved")
        if snap.get("win_rate") is not None:
            msg_parts.append(f"win-rate {round(snap['win_rate'] * 100, 1)}%")
        if sweep.get("ingested"):
            ing = sweep["ingested"]
            msg_parts.append(
                f"new: {ing.get('paper_trades', 0)} paper + {ing.get('predictions', 0)} preds"
            )
        await emit_alert(
            "nightly_sweep",
            title="AI Core Nightly Sweep",
            message="; ".join(msg_parts) or "no resolved trades yet",
            metadata={"stats": snap, "sweep": sweep},
        )
        logger.info(
            "[ai-core] nightly sweep: %s", "; ".join(msg_parts) or "empty",
        )

        # ── Bridge-eligibility advisory ─────────────────────────────
        # Compare every candidate against live on bucket-lift in the
        # 'agent' dimension. Fire a dedup-safe alert per candidate
        # that beats live by ≥ 0.05 lift over ≥ 100 samples. Operator
        # remains the only one who can /promote.
        try:
            live = registry.live
            live_cond = live.conditions_snapshot(min_total=30)
            live_agent_wrs = [r["win_rate"] for r in live_cond.get("agent", []) if r["win_rate"] is not None]
            live_lift = (max(live_agent_wrs) - min(live_agent_wrs)) if len(live_agent_wrs) >= 2 else 0.0
            for engine in registry.all():
                if engine.name == live.name:
                    continue
                if engine.stats_snapshot().get("total_resolved", 0) < 100:
                    continue
                cond = engine.conditions_snapshot(min_total=30)
                wrs = [r["win_rate"] for r in cond.get("agent", []) if r["win_rate"] is not None]
                if len(wrs) < 2:
                    continue
                cand_lift = max(wrs) - min(wrs)
                if cand_lift - live_lift < 0.05:
                    continue
                await emit_alert(
                    f"bridge_eligible_{engine.name}",
                    title=f"AI Core Candidate Eligible: {engine.name}",
                    message=(
                        f"{engine.name} bucket-lift {round(cand_lift, 4)} vs "
                        f"live {round(live_lift, 4)} (Δ +{round(cand_lift - live_lift, 4)}). "
                        f"Manual promotion available via /api/ai-core/engines/{engine.name}/promote."
                    ),
                    metadata={
                        "candidate": engine.name,
                        "live": live.name,
                        "candidate_lift": cand_lift,
                        "live_lift": live_lift,
                        "delta": cand_lift - live_lift,
                        "candidate_total": engine.stats_snapshot().get("total_resolved"),
                    },
                )
        except Exception as e:
            logger.warning(f"[ai-core] bridge-eligibility check failed: {e}")
    except Exception as e:
        logger.warning(f"AI Core nightly sweep failed: {e}")



@app.on_event("startup")
async def startup_event():
    logger.info("=== RISEDUAL AI STARTUP BEGIN ===")
    try:
        wire_db(db)
        app.state.db = db
        logger.info("DB wired successfully")
    except Exception as e:
        logger.exception(f"CRITICAL: DB wire failed: {e}")

    try:
        await _start_schedulers()
        logger.info("Schedulers started")
    except Exception as e:
        logger.warning(f"Scheduler startup failed (non-critical): {e}")

    # AI Promotion History — tamper-evident audit trail. Detect any
    # phase change that happened between the previous boot (env flag
    # flip + restart) and now. Seeds an initial row per core on the
    # very first boot. Never raises.
    try:
        from services.promotion_history import detect_phase_changes_at_startup
        inserted = await detect_phase_changes_at_startup(db)
        if inserted:
            logger.info(
                "[promotion-history] recorded %d transition(s) at boot: %s",
                len(inserted),
                [(r["core"], r["from_phase"], r["to_phase"]) for r in inserted],
            )
    except Exception as e:
        logger.warning(f"Promotion-history boot detector failed (non-critical): {e}")

    # Kraken WebSocket streamer — push-based crypto quotes. Drops
    # the 2s REST cache to sub-100ms freshness when the socket is
    # healthy. Falls back to REST automatically on disconnect or
    # stale snapshots. Gated by ``KRAKEN_WS_STREAM_ENABLED``.
    try:
        from services.kraken_ws_stream import start_kraken_ws_stream
        symbols = ["BTC", "ETH", "SOL", "BNB", "XRP", "ADA",
                   "AVAX", "LINK", "DOGE", "DOT", "MATIC"]
        if start_kraken_ws_stream(symbols):
            logger.info(
                "[kraken_ws] streamer started for %d symbol(s)", len(symbols),
            )
    except Exception as e:
        logger.warning(f"Kraken WS streamer start failed (non-critical): {e}")

    try:
        await create_indexes()
        logger.info("Indexes created")
    except Exception as e:
        logger.warning(f"Index creation failed (non-critical): {e}")

    # Alert-dedup collection indexes (idempotent — safe to call every boot).
    try:
        from services.alert_dedup import ensure_indexes as _alert_dedup_indexes
        await _alert_dedup_indexes(db)
    except Exception as e:
        logger.debug(f"Alert dedup indexes: {e}")

    # ML-adaptation collection indexes (TTL + unique + lookup).
    try:
        from services.model_adaptation import ensure_indexes as _adaptation_indexes
        await _adaptation_indexes(db)
    except Exception as e:
        logger.debug(f"Adaptation indexes: {e}")

    # Crypto signal audit log + web-research cache indexes.
    try:
        from services.crypto_signal_audit import ensure_indexes as _crypto_audit_indexes
        from services.research_router import ensure_indexes as _research_cache_indexes
        from services.adversarial_logger import ensure_indexes as _adv_log_indexes
        await _crypto_audit_indexes(db)
        await _research_cache_indexes(db)
        await _adv_log_indexes(db)
    except Exception as e:
        logger.debug(f"Crypto audit / research-cache / adv-log indexes: {e}")

    # Restore dynamically registered providers from MongoDB
    try:
        from services.providerrouter import ProviderRouter
        cursor = db.registered_providers.find({}, {"_id": 0})
        restored = 0
        async for doc in cursor:
            ProviderRouter.register(doc["lane"], doc)
            restored += 1
        if restored:
            logger.info(f"Restored {restored} dynamically registered providers from DB")
    except Exception as e:
        logger.warning(f"Provider restoration failed (non-critical): {e}")

    # Load encrypted keys from vault into environment
    try:
        from services.key_vault import KeyVault
        vault = KeyVault(db)
        await vault.load_into_env()
    except Exception as e:
        logger.warning(f"Vault key loading failed (non-critical): {e}")

    try:
        await seed_admin()
        logger.info("Admin seed complete")
    except Exception as e:
        logger.warning(f"Admin seed failed (non-critical): {e}")

    # One-shot data migrations — runs only un-applied migrations.
    # Each migration records its success in the `migrations`
    # collection so subsequent boots are no-ops. Failures don't
    # crash startup (the product still serves), but the migration
    # row isn't recorded so the next boot retries.
    try:
        from services.migration_runner import run_pending_migrations
        report = await run_pending_migrations(db)
        logger.info(
            f"Migrations: ran={report['ran']} skipped={report['skipped']} "
            f"errors={report['errors']}"
        )
    except Exception as e:
        logger.warning(f"Migration runner failed (non-critical): {e}")

    try:
        _start_cache_warmup()
    except Exception as e:
        logger.warning(f"Cache warmup failed (non-critical): {e}")

    try:
        _write_test_credentials()
    except Exception as e:
        logger.warning(f"Test credentials write failed (non-critical): {e}")

    # ── ChromaDB rehydrate ────────────────────────────────────────────
    # Mongo is the immutable source of truth. ChromaDB is a deduplicating
    # vector cache that lives on disk under /app/backend/data/chromadb.
    # On a fresh container (ephemeral disk), the vector store starts
    # empty and "similar setup" retrieval is degraded until manually
    # rebuilt. Spawn a non-blocking task to backfill from MongoDB if
    # Chroma is empty. Idempotent — early-exits if Chroma already has
    # episodes. Disable via CHROMA_AUTO_WARMUP=false (e.g. in tests).
    try:
        import asyncio
        asyncio.create_task(_chromadb_warmup())
    except Exception as e:
        logger.warning(f"ChromaDB warmup launch failed (non-critical): {e}")

    logger.info(f"=== RISEDUAL AI STARTUP COMPLETE — {len(app.routes)} routes registered ===")


async def _chromadb_warmup():
    """Rehydrate ChromaDB from MongoDB if the vector store is empty.

    Replays the last 60 days of verified predictions (cap 5000) through
    ``save_regime``. ``save_regime`` upserts by ``_make_id`` so re-runs
    are idempotent. Only acts when Chroma has 0 episodes AND Mongo has
    something to backfill — otherwise it's a no-op.
    """
    if (os.environ.get("CHROMA_AUTO_WARMUP", "true") or "").lower() == "false":
        logger.info("[chroma_warmup] disabled via CHROMA_AUTO_WARMUP=false")
        return
    try:
        from services.market_memory_service import get_memory_stats, save_regime
        from services.prediction_tracker import normalize_confidence
        from services.datetime_utils import to_iso_date
        from datetime import timedelta

        stats = await get_memory_stats()
        chroma_count = stats.get("total_episodes", 0)
        mongo_count = stats.get("mongodb_log_count", 0)

        if chroma_count > 0:
            logger.info(
                f"[chroma_warmup] skip — Chroma already has {chroma_count} episodes"
            )
            return
        if mongo_count == 0:
            logger.info("[chroma_warmup] skip — Mongo log is empty, nothing to rehydrate")
            return

        logger.info(
            f"[chroma_warmup] Chroma empty (Mongo={mongo_count}) — rehydrating from predictions"
        )

        since = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
        cursor = db.predictions.find(
            {
                "verified_24h.correct": {"$in": [True, False]},
                "verified_24h.verified_at": {"$gte": since},
            },
            {"_id": 0},
        ).limit(5000)

        rebuilt = 0
        skipped = 0
        async for p in cursor:
            v24 = p.get("verified_24h") or {}
            try:
                await save_regime({
                    "symbol": p.get("symbol"),
                    # to_iso_date coerces Mongo datetime / ISO str /
                    # None into clean YYYY-MM-DD; replaces the
                    # brittle [:10] slice that silently failed on
                    # datetime fields.
                    "date": to_iso_date(p.get("timestamp")),
                    "price": p.get("price_at_prediction"),
                    "regime": p.get("regime") or {},
                    "confidence": normalize_confidence(p.get("confidence")),
                    "outcome": "hit" if v24.get("correct") is True else "miss",
                    "prediction_id": p.get("prediction_id"),
                })
                rebuilt += 1
            except Exception as exc:
                from services.mongo_chroma_sync_metrics import record_skip
                record_skip(
                    "warmup_save_failed",
                    doc_id=str(p.get("prediction_id") or p.get("symbol") or ""),
                    exc=exc,
                )
                skipped += 1

        # Stamp rebuild metadata so the drift endpoint can
        # distinguish "expected drift right after rebuild" from
        # "drift one hour after rebuild → actively broken".
        # Persisted to Mongo so the stamp survives backend restarts.
        try:
            from services.mongo_chroma_sync_metrics import mark_rebuild
            await mark_rebuild(rebuilt=rebuilt, skipped=skipped, since=since)
        except Exception:  # noqa: BLE001
            pass

        logger.info(
            f"[chroma_warmup] rehydrated {rebuilt} episodes (skipped {skipped})"
        )
    except Exception as e:
        logger.warning(f"[chroma_warmup] failed (non-critical): {e}")


async def _start_schedulers():
    """Start APScheduler jobs for daily digest, watchlist pre-gen, and memory cleanup."""
    # Auto-seed Tier3 universe — runs once on startup. Idempotent
    # (skip if bots already exist), so it's safe to keep enabled
    # across every redeploy. The user shouldn't need to manually
    # curl the seed endpoint from a phone after first deploy.
    #
    # Gated by an env flag so a future operator can disable it
    # if the universe is intentionally being narrowed.
    if os.environ.get("AUTO_SEED_TIER3_UNIVERSE", "true").strip().lower() not in ("0", "false", "off", "no"):
        try:
            from routes.admin_tier3_bootstrap import _TIER3_NEW_TICKERS, _TIER3_DAILY_CAP
            owner_email = os.environ.get("OWNER_EMAIL", "admin@risedual.ai")
            owner = await db.users.find_one(
                {"email": owner_email, "role": "owner"},
                {"_id": 1},
            )
            if owner is not None:
                owner_id = str(owner["_id"])
                from datetime import datetime, timezone
                from uuid import uuid4
                created: list[str] = []
                base_config = {
                    "min_confidence": 70,
                    "strategies": [],
                    "side": "both",
                    "qty": 1,
                    "use_smart_order": True,
                    "auto_sl_pct": 3,
                    "auto_tp_pct": 6,
                    "max_trades_per_day": _TIER3_DAILY_CAP,
                    "trades_today": 0,
                }
                base_stats = {"trades": 0, "pnl": 0, "signals_received": 0, "signals_executed": 0}
                now = datetime.now(timezone.utc).isoformat()
                for ticker in _TIER3_NEW_TICKERS:
                    name = f"Tier3 Accumulator · {ticker}"
                    existing = await db.trading_bots.find_one({"name": name}, {"_id": 1})
                    if existing:
                        continue
                    await db.trading_bots.insert_one({
                        "_id": str(uuid4()).replace("-", ""),
                        "user_id": owner_id,
                        "type": "signal",
                        "name": name,
                        "enabled": True,
                        "mode": "paper",
                        "config": {**base_config, "symbols": [ticker]},
                        "stats": dict(base_stats),
                        "created_at": now,
                        "updated_at": now,
                        "last_run": None,
                    })
                    created.append(ticker)
                # Always normalise the daily cap on existing accumulator bots —
                # cheap, idempotent, ensures the cap stays at the current value
                # if we ever bump it again in code.
                cap_update = await db.trading_bots.update_many(
                    {"name": {"$regex": "^Tier3 Accumulator · "}},
                    {"$set": {
                        "config.max_trades_per_day": _TIER3_DAILY_CAP,
                        "config.trades_today": 0,
                    }},
                )
                if created or cap_update.modified_count:
                    logger.info(
                        "[startup-seed] Tier3 universe: created=%d bumped=%d",
                        len(created), cap_update.modified_count,
                    )
        except Exception as e:
            logger.warning(f"[startup-seed] Tier3 seed failed (non-critical): {e}")

    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from services.digest_service import send_daily_digest
        scheduler = AsyncIOScheduler()
        scheduler.add_job(send_daily_digest, 'cron', hour=6, minute=0, args=[db], id='daily_digest')
        scheduler.add_job(_pregen_watchlist_intel, 'cron', hour=5, minute=30, args=[db], id='watchlist_pregen')
        scheduler.add_job(_run_memory_cleanup, 'cron', hour=2, minute=0, id='memory_cleanup')
        scheduler.add_job(_run_waitlist_auto_invite, 'cron', hour=9, minute=0, id='waitlist_auto_invite')
        scheduler.add_job(_check_smart_orders, 'interval', seconds=30, id='smart_order_monitor')
        scheduler.add_job(_run_grid_bots, 'interval', seconds=30, id='grid_bot_monitor')
        scheduler.add_job(_run_signal_bot_dispatcher, 'interval', minutes=5, id='signal_bot_dispatcher')
        scheduler.add_job(_run_headlines_pipeline, 'interval', minutes=15, id='headlines_pipeline')
        scheduler.add_job(_run_prediction_prewarm, 'interval', minutes=10, id='prediction_prewarm')
        scheduler.add_job(_run_prediction_labeler, 'interval', hours=1, id='prediction_labeler')
        scheduler.add_job(_run_fred_snapshot, 'cron', hour=7, minute=0, id='fred_daily_snapshot')
        scheduler.add_job(_run_13f_scan, 'cron', hour=8, minute=0, id='sec_13f_daily_scan')
        scheduler.add_job(_run_referral_hit_rewards, 'cron', hour=9, minute=0, id='referral_hit_rewards_daily')
        scheduler.add_job(_run_referral_monthly_rewards, 'cron', day=1, hour=9, minute=30, id='referral_monthly_rewards')
        scheduler.add_job(_run_help_search_digest, 'cron', day_of_week='mon', hour=7, minute=0, id='help_search_weekly_digest')
        scheduler.add_job(_run_usaspending_warmup, 'cron', hour=3, minute=30, id='usaspending_warmup')
        scheduler.add_job(_run_nightly_ml_retrain, 'cron', hour=2, minute=30, id='nightly_ml_retrain')
        scheduler.add_job(_run_self_test_monitor, 'interval', minutes=15, id='self_test_monitor')
        scheduler.add_job(_run_conviction_drift_check, 'cron', hour=8, minute=0, id='conviction_drift_check')

        # Daily ticker-abandonment snapshot — runs at 21:05 UTC,
        # right after the regular US session close (matches the
        # existing top-universe-warm-post-close pattern). Belt-and-
        # suspenders alongside the on-read upsert in the admin
        # endpoint: guarantees the Δ-since-yesterday column has
        # data even if nobody opens the admin page on a given day.
        # Picked 21:05 UTC over 01:00 UTC to keep the snapshot's
        # date stamp on the SAME UTC day as the trading session —
        # otherwise the Δ comparison breaks because the session-
        # ending snapshot would land on tomorrow's date.
        # Idempotent (safe to run repeatedly same-day). Never raises.
        async def _run_ticker_abandonment_snapshot():
            try:
                from services.ticker_abandonment_history import snapshot_today
                await snapshot_today(db)
            except Exception:
                logging.exception(
                    "ticker_abandonment_snapshot failed (non-critical)",
                )
        scheduler.add_job(
            _run_ticker_abandonment_snapshot,
            'cron', hour=21, minute=5,
            id='ticker_abandonment_snapshot',
        )

        # Nightly data-integrity tripwire — runs at 03:15 UTC,
        # AFTER memory_cleanup (02:00) and ml_retrain (02:30) so the
        # latest grading / retag is reflected. Writes its summary to
        # the `data_integrity_audits` Mongo collection; admin UI
        # surfaces it via /api/admin/data-integrity/summary. Never
        # mutates production data — tripwire only.
        async def _run_nightly_integrity_audit():
            try:
                from services.data_integrity_auditor import run_nightly_integrity_audit
                await run_nightly_integrity_audit(db, window_hours=24)
            except Exception:
                logging.exception("nightly_integrity_audit failed (non-critical)")
        scheduler.add_job(
            _run_nightly_integrity_audit,
            'cron', hour=3, minute=15,
            id='nightly_integrity_audit',
        )

        # Data-integrity alert evaluator — runs every 15 minutes
        # checking every enabled rule against the same metrics the
        # admin dashboard reads from. Throttle + event log live on
        # the rule documents themselves, so this job is safe to
        # re-run and stateless in memory.
        async def _run_integrity_alert_evaluator():
            try:
                from services.data_integrity_alerts import evaluate_rules_once
                summary = await evaluate_rules_once(db)
                if summary.get("fired", 0) > 0:
                    logging.info(
                        "[integrity_alerts] fired %d rule(s); throttled=%d errors=%d",
                        summary["fired"], summary.get("throttled", 0),
                        summary.get("errors", 0),
                    )
            except Exception:
                logging.exception("integrity_alert_evaluator failed (non-critical)")
        scheduler.add_job(
            _run_integrity_alert_evaluator,
            'interval', minutes=15,
            id='integrity_alert_evaluator',
        )

        # Integrity-mitigation TTL sweep — runs every 5 minutes so
        # the admin dashboard, position sizer, and strong-signal
        # suppressor see an expired mitigation within ≤5min of its
        # TTL instead of waiting for the next alert evaluation.
        # Also primes the sync-side cache used by pure-math sizing
        # paths that can't await. No-ops when nothing is active.
        async def _run_integrity_mitigation_sweep():
            try:
                from services.integrity_mitigation_service import (
                    expire_integrity_mitigations,
                    refresh_sync_cache,
                )
                await expire_integrity_mitigations(db)
                await refresh_sync_cache(db)
            except Exception:
                logging.exception("integrity_mitigation_sweep failed (non-critical)")
        scheduler.add_job(
            _run_integrity_mitigation_sweep,
            'interval', minutes=5,
            id='integrity_mitigation_sweep',
        )

        # Notification lifecycle sweep — runs the registered
        # superseders (toxic_spike, verdict_change, ...) twice daily
        # so stale alerts disappear from the drawer without waiting
        # for an admin button-click or a regrade-backfill run. Cheap
        # operation: re-checks source rows, supersedes only when the
        # source no longer supports the alert. Idempotent — re-runs
        # are safe. Each run writes a receipt to
        # ``notification_lifecycle_runs`` with trigger=``scheduled_cron``.
        # Cadence: 04:00 UTC (post-overnight backfills) and 16:00 UTC
        # (pre-US-close) so an alert that becomes stale during the
        # session is cleared within ~12 hours.
        async def _run_notification_lifecycle_sweep():
            try:
                from services.notification_lifecycle import supersede_stale_alerts
                summary = await supersede_stale_alerts(
                    db, trigger="scheduled_cron",
                )
                totals = summary.get("totals") or {}
                if totals.get("superseded", 0) > 0:
                    logging.info(
                        "[notif_lifecycle] superseded=%d kept_active=%d checked=%d",
                        totals.get("superseded", 0),
                        totals.get("kept_active", 0),
                        totals.get("checked", 0),
                    )
            except Exception:
                logging.exception(
                    "notification_lifecycle_sweep failed (non-critical)"
                )
        scheduler.add_job(
            _run_notification_lifecycle_sweep,
            'cron', hour='4,16', minute=0,
            id='notification_lifecycle_sweep',
        )

        # NEWS_SHOCK feeders — drives both Benzinga (news volume) and
        # Alpha Vantage (sentiment) telemetry population on a 15-min
        # market-hours cadence over a rotating slice of Tier A. The
        # market-hours gate is inside the tick, so this job is safe to
        # run every 15 min around the clock — it no-ops outside RTH
        # without consuming any provider quota.
        async def _run_news_feeders_tick():
            try:
                from services.news_feeders_scheduler import run_news_feeders_tick
                summary = await run_news_feeders_tick(db)
                if summary.get("status") == "ran":
                    logging.info(
                        "[news_feeders] fed=%d+%d skipped=%d+%d offset→%d",
                        summary.get("benzinga", {}).get("fed", 0),
                        summary.get("av", {}).get("fed", 0),
                        summary.get("benzinga", {}).get("skipped", 0),
                        summary.get("av", {}).get("skipped", 0),
                        summary.get("new_offset", -1),
                    )
            except Exception:
                logging.exception("news_feeders_tick failed (non-critical)")
        scheduler.add_job(
            _run_news_feeders_tick,
            'interval', minutes=15,
            id='news_feeders_tick',
        )

        # Kraken xStock equity shadow comparator — Phase 0 (market data
        # only, no orders). Hits Kraken's public Ticker + AssetPairs
        # endpoints and shadow-compares against our primary Alpaca/AV
        # quote for the top-N ML watchlist + S&P500 universe. Writes
        # one row per (symbol × tick) into ``kraken_equity_shadow_compare``.
        # GATED: the inner ``run_kraken_shadow_compare_once`` short-circuits
        # unless ``KRAKEN_SHADOW_ENABLED`` is set, so this job is safe
        # to register unconditionally — cold-start pods stay silent.
        async def _run_kraken_shadow_compare():
            try:
                from services.kraken_equity_shadow_service import (
                    ensure_indexes,
                    run_kraken_shadow_compare_once,
                )
                # Cheap idempotent index pass — safe every tick.
                await ensure_indexes(db)
                summary = await run_kraken_shadow_compare_once(db)
                if summary.get("ok") and summary.get("rows_written", 0) > 0:
                    logging.info(
                        "[kraken_shadow] tick rows=%d alerts=%d session=%s",
                        summary.get("rows_written", 0),
                        summary.get("alerts_fired", 0),
                        summary.get("session"),
                    )
            except Exception:
                logging.exception("kraken_shadow_compare failed (non-critical)")
        scheduler.add_job(
            _run_kraken_shadow_compare,
            'interval', minutes=5,
            id='kraken_shadow_compare',
        )

        # Sovereign AI Resolution Loop — back-patches
        # sovereign_decisions.outcomes.{60m|4h|eod} from closed paper_trades
        # via the sovereign_decision_id link. Every 15 min, batch cap 50 per
        # (horizon, asset) slice so a backlog can't starve the rest of the
        # fleet. Never raises — internal exceptions become structured logs.
        async def _run_sovereign_resolution_tick():
            try:
                from services.sovereign_resolution_loop import run_resolution_tick
                summary = await run_resolution_tick(db)
                if summary.get("total_resolved", 0):
                    logging.info(
                        "[sovereign_resolve] resolved=%d per_horizon=%s",
                        summary.get("total_resolved", 0),
                        [(t["horizon"], t["asset_type"], t["resolved"])
                            for t in summary.get("per_horizon", [])],
                    )
            except Exception:
                logging.exception("sovereign_resolution_tick failed (non-critical)")
        scheduler.add_job(
            _run_sovereign_resolution_tick,
            'interval', minutes=15,
            id='sovereign_resolution_tick',
        )

        # Fear & Greed history refresh — daily at 04:30 UTC (after the
        # alternative.me daily snapshot rolls over). Pulls the last 30
        # days and upserts them so any backfills/corrections from the
        # source are reflected. Lazy seeding on first dashboard hit
        # handles cold-start; this job keeps the store fresh thereafter.
        async def _run_fear_greed_refresh():
            try:
                from services.fear_greed_service import refresh_fear_greed_history
                n = await refresh_fear_greed_history()
                logging.info(f"[fear_greed] daily refresh wrote {n} rows")
            except Exception:
                logging.exception("fear_greed_refresh failed (non-critical)")
        scheduler.add_job(
            _run_fear_greed_refresh,
            'cron', hour=4, minute=30,
            id='fear_greed_refresh',
        )
        scheduler.add_job(_run_tier3_readiness_digest, 'cron', hour=8, minute=15, id='tier3_readiness_digest')
        scheduler.add_job(_run_ml_health_digest, 'cron', hour=8, minute=0, id='ml_health_digest')
        scheduler.add_job(_run_paper_trade_closer, 'interval', minutes=60, id='paper_trade_closer')
        # ── Crypto bot (24/7 lane, isolated from equity ml_paper_trader) ──
        scheduler.add_job(_run_crypto_paper_bot, 'interval', minutes=15,
                          id='crypto_paper_bot', replace_existing=True)
        # ── Crypto closer (own service, 15-min tick, 12h hold window) ──
        scheduler.add_job(_run_crypto_paper_closer, 'interval', minutes=15,
                          id='crypto_paper_trade_closer', replace_existing=True)
        # ── Crypto adaptation detector (closed-loop learning, 6-hourly) ──
        scheduler.add_job(_run_crypto_adaptation_detector, 'interval', hours=6,
                          id='crypto_adaptation_detector', replace_existing=True)
        # ── Day-trade scanner (scan → rank → gate → queue top-1) ──
        # Strict discipline: never execute while scanning. ALWAYS scan
        # all symbols first, THEN rank, THEN decide. Two parallel lanes
        # (equity + crypto) at 5-min cadence. Scanner writes only to
        # ``day_trade_targets`` + ``day_trade_scan_log`` — no live
        # trade mutation. Gated by ``DAY_TRADE_SCANNER_ENABLED``.
        scheduler.add_job(
            _run_day_trade_scanner_equity, 'interval', minutes=5,
            id='day_trade_scanner_equity', replace_existing=True,
        )
        scheduler.add_job(
            _run_day_trade_scanner_crypto, 'interval', minutes=5,
            id='day_trade_scanner_crypto', replace_existing=True,
        )
        # ── Day-trade EOD exit monitor (closes max_hold_until breaches) ──
        scheduler.add_job(
            _run_day_trade_exit_monitor, 'interval', minutes=5,
            id='day_trade_exit_monitor', replace_existing=True,
        )
        # ── Cross-asset stress monitor — checks Spread Watch every
        # minute. Fires stress_events row + (optionally) auto-flattens
        # when ≥ STRESS_SYMBOL_THRESHOLD symbols are stressed. Per-event
        # cooldown lives on the row so process restarts respect it. ──
        scheduler.add_job(
            _run_stress_event_monitor, 'interval', minutes=1,
            id='stress_event_monitor', replace_existing=True,
        )
        # ── Tier-3 slippage advisor — weekly segmentation pass.
        # Mondays at 13:15 UTC (just before US RTH) so the proposals
        # land in time for the operator to flip env knobs before the
        # session opens. Advisory only — never applies env changes. ──
        scheduler.add_job(
            _run_tier3_slippage_advisor, 'cron',
            day_of_week='mon', hour=13, minute=15,
            id='tier3_slippage_advisor', replace_existing=True,
        )
        # ── Research Shadow scorer (Tier-3 safe; writes only to
        # research_shadow_decisions; deferred counterfactual scoring) ──
        scheduler.add_job(_run_research_shadow_scorer, 'interval', seconds=60,
                          id='research_shadow_scorer', replace_existing=True)
        # ── Autonomous trading agents (all narrate into agent_activity) ──
        # Trading agents — staggered so they don't hammer yfinance
        # simultaneously. Mean-rev runs most often; earnings only
        # needs once daily pre-market.
        scheduler.add_job(_run_agent_mean_reversion, 'interval', minutes=15,
                          id='agent_mean_reversion')
        scheduler.add_job(_run_agent_options_paper, 'interval', minutes=30,
                          id='agent_options_paper')
        scheduler.add_job(_run_agent_earnings_watchdog, 'cron',
                          hour=8, minute=30, id='agent_earnings_watchdog')
        # Observer agents — daily rollups
        scheduler.add_job(_run_agent_regime_drift, 'cron',
                          hour=8, minute=45, id='agent_regime_drift')
        scheduler.add_job(_run_agent_performance_monitor, 'cron',
                          hour=8, minute=50, id='agent_performance_monitor')
        # ── Patent Watch (USPTO daily fetch) ──
        scheduler.add_job(_run_patent_watch_refresh, 'cron',
                          hour=4, minute=15, id='patent_watch_refresh',
                          replace_existing=True)
        # ── Ops alerter (wedge detector — every 15 minutes) ──
        # No-op unless OPS_ALERT_WEBHOOK_URL is set, so safe to
        # always schedule.
        scheduler.add_job(_run_ops_alerter_tick, 'interval',
                          minutes=15, id='ops_alerter_tick',
                          replace_existing=True)
        # ── AI Core nightly sweep (02:45 UTC) ──
        # Runs after memory cleanup (02:00) and ML retrain (02:30) so
        # any newly-graded predictions / closed paper trades are
        # already in place. Dedup-safe — re-runs on the same day are
        # idempotent thanks to the unique alert id.
        scheduler.add_job(_run_ai_core_nightly, 'cron',
                          hour=2, minute=45, id='ai_core_nightly',
                          replace_existing=True)
        # ── Position reconciler (Step 10 OUTCOME_VERIFIED for
        # external broker fills — equity + options, every 30 min) ──
        # No-op when no rows have proof_chain_entity_id pending; the
        # initial query is index-friendly and bounded at 200 rows.
        scheduler.add_job(_run_position_reconciler, 'interval',
                          minutes=30, id='position_reconciler',
                          replace_existing=True)
        # ── Mongo→Chroma drift alert watcher (every 5 min) ──
        # Same compute path as GET /api/admin/memory/drift. Emits
        # to ``ai_core_alerts`` collection only on threshold
        # crossings / jumps > 5pts / recovery — dedup-bucketed by
        # UTC day, so this is *not* a spam source.
        scheduler.add_job(_run_drift_alert_watcher, 'interval',
                          minutes=5, id='drift_alert_watcher',
                          replace_existing=True)
        # ── ETL framework jobs (one scheduler entry per registered
        # subclass of BaseETLJob; pulled from the in-process registry).
        # Each job owns its own unique composite + TTL index; rows
        # expire at retention_days via Mongo server-side TTL. The
        # scheduler entry per job uses the job's declared ``cadence``
        # dict as cron kwargs, so e.g. ``{"day_of_week": "mon",
        # "hour": 4}`` runs Mondays at 4 UTC. Disabled jobs are
        # skipped at scheduling time.
        try:
            from services.etl_registry import all_jobs as _all_etl_jobs
            for _etl_job in _all_etl_jobs():
                if not _etl_job.enabled:
                    continue
                scheduler.add_job(
                    _run_etl_job,
                    'cron',
                    args=[_etl_job.source_name],
                    id=f'etl_{_etl_job.source_name}',
                    replace_existing=True,
                    **_etl_job.cadence,
                )
        except Exception as _e:  # noqa: BLE001
            logger.warning(f"ETL scheduler setup failed: {_e}")

        # ── Top-universe tiered pre-warm (user-requested Phase 1) ──
        # Three jobs, all non-critical — a failure here never blocks
        # anything downstream because they only *seed* caches that
        # services already check-through-and-fall-back.
        #   - rebuild:       Sunday 00:00 UTC (~500 OVERVIEW calls, weekly)
        #   - warm post-close: 21:05 UTC daily (Tier A full, Tier B light)
        #   - warm pre-open:   13:00 UTC daily (Tier A quote + technicals)
        async def _run_universe_rebuild():
            try:
                from services.top_universe_service import rebuild_universe
                await rebuild_universe(db)
            except Exception:
                logging.exception("top_universe_rebuild failed (non-critical)")

        async def _run_universe_warm_post_close():
            try:
                from services.top_universe_service import warm_universe
                await warm_universe(db, run_type="post_close")
            except Exception:
                logging.exception("top_universe_warm_post_close failed (non-critical)")

        async def _run_universe_warm_pre_open():
            try:
                from services.top_universe_service import warm_universe
                await warm_universe(db, run_type="pre_open")
            except Exception:
                logging.exception("top_universe_warm_pre_open failed (non-critical)")

        scheduler.add_job(
            _run_universe_rebuild,
            'cron', day_of_week='sun', hour=0, minute=0,
            id='top_universe_rebuild', replace_existing=True,
        )
        scheduler.add_job(
            _run_universe_warm_post_close,
            'cron', hour=21, minute=5,
            id='top_universe_warm_post_close', replace_existing=True,
        )
        scheduler.add_job(
            _run_universe_warm_pre_open,
            'cron', hour=13, minute=0,
            id='top_universe_warm_pre_open', replace_existing=True,
        )

        # Options universe warm — fires every 5 min. The service itself
        # is market-hours-gated (13:30–21:00 UTC Mon–Fri); off-hours calls
        # write a "skipped" stats row and exit fast, so the 5-min cadence
        # keeps the snapshot fresh during session without burning quota
        # on static overnight data.
        async def _run_options_universe_warm():
            try:
                from services.options_universe_service import warm_options_universe
                await warm_options_universe(db)
            except Exception:
                logging.exception("options_universe_warm failed (non-critical)")

        scheduler.add_job(
            _run_options_universe_warm,
            'interval', minutes=5,
            id='options_universe_warm', replace_existing=True,
        )

        # Dedicated scheduler heartbeat — writes every 60s to a tiny
        # ``scheduler_heartbeat`` document. Replaces the old "infer
        # heartbeat from arbitrary other writes" proxy which would
        # show false-stale on quiet pods (e.g. when no paper trades
        # opened in the last day, the proxy reported the scheduler as
        # dead even though it was firing every minute).
        async def _write_scheduler_heartbeat():
            try:
                from datetime import datetime, timezone
                await db.scheduler_heartbeat.update_one(
                    {"_id": "main"},
                    {
                        "$set": {
                            "last_beat_at": datetime.now(timezone.utc),
                            "host_pid": os.getpid(),
                        },
                        "$inc": {"beat_count": 1},
                    },
                    upsert=True,
                )
            except Exception:  # noqa: BLE001
                logging.exception("scheduler_heartbeat write failed")

        scheduler.add_job(
            _write_scheduler_heartbeat,
            'interval', seconds=60,
            id='scheduler_heartbeat', replace_existing=True,
            next_run_time=datetime.now(timezone.utc),  # write one immediately
        )

        scheduler.start()
        # Expose the started scheduler to the self-test route so its
        # /api/admin/self-test probe can check job registration health.
        try:
            from routes.self_test import set_scheduler as _set_self_test_scheduler
            _set_self_test_scheduler(scheduler)
        except Exception as e:
            logger.warning(f"Self-test scheduler wire failed: {e}")
        logger.info("Schedulers started: digest (6:00), watchlist (5:30), memory cleanup (2:00), nightly ML retrain (2:30), waitlist invite (9:00), smart orders (30s), grid bots (30s), signal dispatcher (5m), headlines (15m), predictions (10m), ML labeler (1h), FRED snapshot (7:00), 13F scan (8:00), referral hit rewards (9:00 daily), referral monthly rewards (1st @ 9:30), help search digest (Mon 7:00), USASpending warmup (3:30), self-test monitor (15m), conviction drift (8:00), tier3 digest (8:15), ML health digest (8:00), paper-trade closer (60m), crypto paper bot (15m, 24/7), crypto closer (15m, 12h hold), crypto adaptation detector (6h), position reconciler (30m), drift alert watcher (5m), top-universe rebuild (Sun 00:00), top-universe warm post-close (21:05), top-universe warm pre-open (13:00), options-universe warm (5m, market-hours-gated), notification lifecycle sweep (4:00 + 16:00)")
    except Exception as e:
        logger.warning(f"Scheduler setup failed: {e}")


async def _check_smart_orders():
    """Background: Monitor smart orders and trigger SL/TP/ladder fills."""
    try:
        from services.smart_order_service import check_smart_orders
        await check_smart_orders()
    except Exception as e:
        logger.debug(f"Smart order check error: {e}")


async def _run_position_reconciler():
    """Background: append OUTCOME_VERIFIED proof blocks for filled live
    broker / options orders that the broker now reports as closed.

    Wraps the entire sweep in try/except so a single broker outage
    can't kill the scheduler thread. Logs structured summary (only
    when ``processed > 0``) so quiet runs don't spam the log.
    """
    try:
        from services.position_reconciler import run_position_reconciler
        await run_position_reconciler(db)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Position reconciler tick failed: {e}")


async def _run_drift_alert_watcher():
    """Background: detect Mongo→Chroma sync regressions and emit
    alerts to the ``ai_core_alerts`` collection on threshold
    crossings or sudden jumps. The watcher is dedup-bucketed by
    UTC day so persistent drift fires ONE alert/day, not 288."""
    try:
        from services.drift_alert_watcher import check_and_alert
        result = await check_and_alert()
        fired = result.get("fired") or []
        if fired:
            logger.info(
                "[drift_alert] tick pct=%s fired=%s",
                result.get("current_pct"), ",".join(fired),
            )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Drift alert watcher tick failed: {e}")


async def _run_etl_job(source_name: str):
    """APScheduler entry point for a single registered ETL job.

    Dispatches into the framework's ``BaseETLJob.run(db)`` method.
    Non-raising — the framework itself captures all exceptions
    into the run summary + audit log.
    """
    try:
        from services.etl_registry import get_job
        job = get_job(source_name)
        if job is None:
            logger.warning(
                "[etl] scheduler fired for '%s' but no job is registered",
                source_name,
            )
            return
        await job.run(db, trigger="cron")
    except Exception as exc:  # noqa: BLE001
        # Defence-in-depth — the framework is supposed to catch
        # everything itself, but a bug in the framework must not
        # take down the scheduler.
        logger.warning(
            "[etl] scheduler wrapper for '%s' raised: %s",
            source_name, exc,
        )


async def _run_grid_bots():
    """Background: Run all enabled grid bots."""
    try:
        from services.trading_bot_service import run_grid_bots
        await run_grid_bots()
    except Exception as e:
        logger.debug(f"Grid bot error: {e}")


async def _run_signal_bot_dispatcher():
    """Background: Fan scanner results into all enabled signal bots."""
    try:
        from services.trading_bot_service import run_signal_bot_dispatcher
        result = await run_signal_bot_dispatcher()
        if result and result.get("dispatched"):
            logger.info(
                f"Signal-bot dispatcher: {result['dispatched']} trades "
                f"across {result.get('users', 0)} users, "
                f"{result.get('symbols_scanned', 0)} symbols scanned"
            )
    except Exception as e:
        logger.debug(f"Signal dispatcher error: {e}")


async def _run_conviction_drift_check():
    """Background: Daily week-over-week win-rate drop detector for
    conviction buckets. Emails the owner when a bucket drops ≥20pp."""
    try:
        from services.conviction_drift_alerts import run_conviction_drift_check
        result = await run_conviction_drift_check(db)
        if result and result.get("sent"):
            logger.info(
                f"Conviction drift: {result.get('fresh', 0)} new alert(s) "
                f"sent to {result.get('owner')}"
            )
    except Exception as e:
        logger.debug(f"Conviction drift check error: {e}")


async def _run_tier3_readiness_digest():
    """Background: Daily one-line Tier 3 readiness pulse to the owner.
    Turns the 30-day unlock gate into a visible streak."""
    try:
        from services.tier3_readiness_digest import run_tier3_readiness_digest
        result = await run_tier3_readiness_digest(db)
        if result.get("sent"):
            logger.info(
                "Tier 3 digest sent: score=%.1f delta=%s blockers=%d",
                result.get("score", 0.0),
                result.get("delta"),
                result.get("blockers", 0),
            )
    except Exception as e:
        logger.debug(f"Tier 3 readiness digest error: {e}")


async def _run_ml_health_digest():
    """Background: Daily ML safety-rail health brief to the owner
    (08:00 UTC). Surfaces 24h soften/revert/shadow counts, top
    toxic-metric triggers, active-rule roster, and the latest
    tuning recommendation."""
    try:
        from services.ml_health_digest_service import run_ml_health_digest
        result = await run_ml_health_digest(db)
        if result.get("sent"):
            logger.info(
                "ML health digest sent: recipient=%s counts=%s active=%d",
                result.get("recipient"),
                result.get("counts"),
                result.get("active_count", 0),
            )
    except Exception as e:
        logger.debug(f"ML health digest error: {e}")


async def _run_crypto_paper_bot():
    """Background: 24/7 crypto paper-trading bot. Isolated from the
    equity ml_paper_trader / paper_trade_closer pipeline — fills land
    in the ``crypto_paper_trades`` collection only.

    Uses the dedicated ``services.crypto_quotes.get_crypto_quote``
    wrapper, never the equity ``get_quote`` path. Errors are logged
    and swallowed so a quote outage on one symbol can't take down
    the scheduler tick."""
    try:
        from services.crypto_paper_trader import run_crypto_paper_bot
        from services.crypto_quotes import get_crypto_quote, get_crypto_history
        # Universe loaded from the bot's own canonical list (8 symbols
        # as of 2026-05-02). Single source of truth lives in
        # services.crypto_paper_trader.CRYPTO_SYMBOLS.
        from services.crypto_paper_trader import CRYPTO_SYMBOLS
        results = await run_crypto_paper_bot(
            db=db,
            quote_provider=get_crypto_quote,
            history_provider=get_crypto_history,
            symbols=CRYPTO_SYMBOLS,
        )
        opened = sum(1 for r in results if r.get("status") == "open")
        skipped = sum(1 for r in results if r.get("skipped"))
        if opened or skipped:
            logger.info(
                "Crypto paper bot: opened=%d skipped=%d",
                opened, skipped,
            )
    except Exception as e:
        logger.debug(f"Crypto paper bot error: {e}")


async def _run_crypto_paper_closer():
    """Background: 24/7 crypto paper-trade closer. Closes any
    open crypto fill that has aged past the max hold window
    (default 12h, env-tunable via CRYPTO_PAPER_MAX_HOLD_HOURS).

    On close, hands the trade to ``crypto_memory_writer`` which
    routes it to the isolated ``crypto_trade_memory`` learning
    surface — that's the input the adaptation detector consumes."""
    try:
        from services.crypto_closer import close_expired_crypto_trades
        from services.crypto_quotes import get_crypto_quote
        import os
        hold_hours = int(os.environ.get("CRYPTO_PAPER_MAX_HOLD_HOURS", "12"))
        summary = await close_expired_crypto_trades(
            db=db, quote_provider=get_crypto_quote, hold_hours=hold_hours,
        )
        if summary.get("closed") or summary.get("errors"):
            logger.info(
                "Crypto paper closer: closed=%d skipped=%d errors=%d hold=%dh",
                summary.get("closed", 0),
                summary.get("skipped", 0),
                summary.get("errors", 0),
                summary.get("hold_hours", 0),
            )
    except Exception as e:
        logger.debug(f"Crypto paper closer error: {e}")


async def _run_crypto_adaptation_detector():
    """Background: scan ``crypto_trade_memory`` for repeated failure
    patterns and emit ``crypto_model_adaptations`` rows that the
    bot's signal layer applies at decision time. Runs every 6h
    so a single bad day doesn't whipsaw the live signal."""
    try:
        from services.crypto_adaptation_service import detect_crypto_adaptations
        created = await detect_crypto_adaptations(db)
        if created:
            logger.info(
                "Crypto adaptations: created=%d (keys=%s)",
                len(created),
                [f"{a['failure_code']}:{a['regime']}" for a in created],
            )
    except Exception as e:
        logger.debug(f"Crypto adaptation detector error: {e}")


async def _run_day_trade_scanner_equity():
    """Background: scan all equity predictions, rank, gate, queue
    the single highest-scoring survivor as a day-trade target.

    Read-mostly: phases 1-3 issue zero writes to ``paper_trades``.
    Phase 4 writes one ``day_trade_targets`` row (the chosen winner,
    if any) and one ``day_trade_scan_log`` row (audit trail).
    Gated by ``DAY_TRADE_SCANNER_ENABLED`` (default on)."""
    if os.environ.get("DAY_TRADE_SCANNER_ENABLED", "1").strip().lower() in (
        "0", "false", "off", "no",
    ):
        return
    try:
        from services.day_trade_scanner import run_scan
        result = await run_scan(db, "equity")
        if result.chosen is not None:
            logger.info(
                "[day_trade_scan] equity winner=%s score=%.4f blocked=%d total=%d",
                result.chosen.symbol, result.chosen.score,
                result.blocked_count, result.total_scanned,
            )
    except Exception as e:
        logger.debug(f"Day-trade scanner (equity) error: {e}")


async def _run_day_trade_scanner_crypto():
    """Background: same scan → rank → gate → queue pipeline as the
    equity scanner, scoped to crypto predictions."""
    if os.environ.get("DAY_TRADE_SCANNER_ENABLED", "1").strip().lower() in (
        "0", "false", "off", "no",
    ):
        return
    try:
        from services.day_trade_scanner import run_scan
        result = await run_scan(db, "crypto")
        if result.chosen is not None:
            logger.info(
                "[day_trade_scan] crypto winner=%s score=%.4f blocked=%d total=%d",
                result.chosen.symbol, result.chosen.score,
                result.blocked_count, result.total_scanned,
            )
    except Exception as e:
        logger.debug(f"Day-trade scanner (crypto) error: {e}")


async def _run_day_trade_exit_monitor():
    """Background: close day-trade positions whose ``max_hold_until``
    (21:00 UTC EOD) has elapsed. Idempotent — re-running after the
    close is a no-op."""
    if os.environ.get("DAY_TRADE_SCANNER_ENABLED", "1").strip().lower() in (
        "0", "false", "off", "no",
    ):
        return
    try:
        from services.day_trade_exit_monitor import expire_due_day_trades
        counts = await expire_due_day_trades(db)
        if counts.get("equity_closed") or counts.get("crypto_closed"):
            logger.info(
                "[day_trade_exit] equity=%d crypto=%d errors=%d",
                counts["equity_closed"], counts["crypto_closed"],
                counts.get("errors", 0),
            )
    except Exception as e:
        logger.debug(f"Day-trade exit monitor error: {e}")

async def _run_tier3_slippage_advisor():
    """Background: weekly slippage segmentation → drafts env-tweak
    proposals into ``tier3_advisor_proposals`` (advisory only —
    operator review required to apply)."""
    if os.environ.get("TIER3_ADVISOR_ENABLED", "1").strip().lower() in (
        "0", "false", "off", "no",
    ):
        return
    try:
        from services.tier3_slippage_advisor import run_advisor_cycle
        result = await run_advisor_cycle(db, lookback_days=30)
        if result.get("inserted", 0) > 0:
            logger.info(
                "[tier3-advisor] inserted=%d deduped=%d outliers=%d",
                result["inserted"], result.get("deduped", 0),
                result.get("outliers_found", 0),
            )
    except Exception as e:
        logger.debug(f"Tier-3 slippage advisor error: {e}")



async def _run_stress_event_monitor():
    """Background: check Spread Watch for cross-asset liquidity
    stress; fire ``stress_events`` rows + auto-flatten when the
    threshold + cooldown gates allow."""
    if os.environ.get("STRESS_MONITOR_ENABLED", "1").strip().lower() in (
        "0", "false", "off", "no",
    ):
        return
    try:
        from services.stress_event_monitor import run_stress_check
        result = await run_stress_check(db)
        if result.get("fired"):
            logger.info(
                "[stress] FIRED — stressed=%d session=%s flatten=%s",
                result.get("stressed_count"),
                result.get("session"),
                result.get("flatten_result"),
            )
    except Exception as e:
        logger.debug(f"Stress event monitor error: {e}")




async def _run_paper_trade_closer():
    """Background: Hourly. Close AI-driven `paper_trades` whose
    open age exceeds the hold window (default 24h). Plugs the
    gap that left 5 trades stuck for 9 days on 2026-04-25 — the
    `prediction_labeler` only updates `predictions`, never the
    paper_trades collection itself."""
    try:
        from services.paper_trade_closer import close_due_paper_trades
        result = await close_due_paper_trades(db)
        if result.get("closed") or result.get("holds") or result.get("errors"):
            logger.info(
                "Paper-trade closer: closed=%d holds=%d errors=%d hold_hours=%s",
                result.get("closed", 0),
                result.get("holds", 0),
                result.get("errors", 0),
                result.get("hold_hours"),
            )
    except Exception as e:
        logger.debug(f"Paper trade closer error: {e}")



# ── Autonomous trading + observer agents ────────────────────────────
# Each wrapper is defensive: any uncaught failure is logged but never
# bubbles up to APScheduler (a raise there would poison the job state
# and kill future runs). All narration happens inside the agent itself.

async def _run_agent_mean_reversion():
    """Scheduled: scan large-cap watchlist for 2σ SMA dislocations."""
    try:
        from services.trading_agents import mean_reversion
        await mean_reversion.run(db)
    except Exception as e:
        logger.debug(f"agent mean_reversion error: {e}")


async def _run_agent_options_paper():
    """Scheduled: mirror high-conviction ML signals into paper options."""
    try:
        from services.trading_agents import options_paper
        await options_paper.run(db)
    except Exception as e:
        logger.debug(f"agent options_paper error: {e}")


async def _run_agent_earnings_watchdog():
    """Scheduled daily: contrarian entries on pre-earnings drift."""
    try:
        from services.trading_agents import earnings_watchdog
        await earnings_watchdog.run(db)
    except Exception as e:
        logger.debug(f"agent earnings_watchdog error: {e}")


async def _run_agent_regime_drift():
    """Scheduled daily: compare feature stability week-over-week."""
    try:
        from services.trading_agents.observers import regime_drift_check
        await regime_drift_check(db)
    except Exception as e:
        logger.debug(f"agent regime_drift error: {e}")


async def _run_agent_performance_monitor():
    """Scheduled daily: alert on degraded strategy performance."""
    try:
        from services.trading_agents.observers import performance_monitor_check
        await performance_monitor_check(db)
    except Exception as e:
        logger.debug(f"agent performance_monitor error: {e}")



async def _run_headlines_pipeline():
    """Background: Scrape, clean, and store financial headlines every 15 minutes."""
    try:
        from services.headlines_pipeline import HeadlinesPipeline
        pipeline = HeadlinesPipeline(db)
        result = await pipeline.run_cycle()
        if result.get("total_new", 0) > 0:
            logger.info(f"Headlines pipeline: {result['total_new']} new / {result['total_scraped']} scraped")
    except Exception as e:
        logger.debug(f"Headlines pipeline error: {e}")


async def _run_prediction_prewarm():
    """Background: Keep predictions warm every 10 minutes."""
    try:
        from routes.market_data import ensure_prediction_refresh
        await ensure_prediction_refresh()
    except Exception as e:
        logger.debug(f"Prediction prewarm error: {e}")


async def _run_prediction_labeler():
    """Background: Label FeaturesSnapshots with ground-truth outcomes (hourly)."""
    try:
        from services.prediction_labeler import label_pending_snapshots
        await label_pending_snapshots(db)
    except Exception as e:
        logger.debug(f"Prediction labeler error: {e}")


async def _run_fred_snapshot():
    """Background: Store daily FRED macro indicators snapshot to MongoDB (7:00 AM UTC).
    Also stores ALFRED vintage data for revision-prone series."""
    try:
        from services.fred_service import get_macro_indicators, REVISION_WATCH_SERIES, _fetch_vintage, _get_key
        from datetime import datetime, timezone

        data = await get_macro_indicators()
        if data.get("error") or not data.get("indicators"):
            logger.warning(f"FRED snapshot skipped: {data.get('error', 'no indicators')}")
            return

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # Check if we already snapshotted today
        existing = await db.fred_snapshots.find_one({"date": today})
        if existing:
            logger.debug("FRED snapshot already exists for today")
            return

        snapshot = {
            "date": today,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "indicators": {},
            "vintages": {},
        }

        for ind in data["indicators"]:
            snapshot["indicators"][ind["id"]] = {
                "name": ind["name"],
                "category": ind["category"],
                "value": ind["raw_value"],
                "display_value": ind["value"],
                "unit": ind["unit"],
                "date": ind["date"],
                "change_pct": ind.get("change_pct"),
            }

        # Fetch ALFRED vintage for revision-watch series (as-reported today)
        import httpx
        key = _get_key()
        if key:
            async with httpx.AsyncClient(timeout=20) as client:
                for sid in REVISION_WATCH_SERIES:
                    try:
                        vdata = await _fetch_vintage(client, sid, key, today, limit=3)
                        if vdata and vdata.get("observations"):
                            snapshot["vintages"][sid] = [
                                {"date": o["date"], "value": float(o["value"])}
                                for o in vdata["observations"]
                                if o.get("value") not in (".", "", None)
                            ]
                    except Exception:
                        pass

        # Check for revisions against previous snapshot
        prev_snap = await db.fred_snapshots.find_one(
            {"date": {"$lt": today}}, sort=[("date", -1)], projection={"_id": 0}
        )
        if prev_snap:
            revision_alerts = []
            prev_inds = prev_snap.get("indicators", {})
            for sid, curr in snapshot["indicators"].items():
                prev = prev_inds.get(sid)
                if prev and prev.get("value") is not None and curr.get("value") is not None:
                    if abs(curr["value"] - prev["value"]) > 0.001:
                        revision_alerts.append({
                            "series_id": sid,
                            "name": curr["name"],
                            "old_value": prev["value"],
                            "new_value": curr["value"],
                            "revision": round(curr["value"] - prev["value"], 4),
                        })
            if revision_alerts:
                snapshot["revision_alerts"] = revision_alerts
                logger.info(f"FRED revision alerts: {len(revision_alerts)} indicators revised")

        await db.fred_snapshots.insert_one(snapshot)
        logger.info(f"FRED daily snapshot saved: {len(data['indicators'])} indicators, {len(snapshot.get('vintages', {}))} vintages for {today}")
    except Exception as e:
        logger.warning(f"FRED snapshot error: {e}")


async def _run_13f_scan():
    """Background: Daily SEC 13F scan — refreshes top institutions' filings and
    emits alerts for watchlist symbols (8:00 AM UTC)."""
    try:
        from services.sec_13f_service import scan_and_alert
        result = await scan_and_alert(db)
        logger.info(f"13F scan complete: refreshed={result.get('refreshed',0)}, alerts_created={result.get('alerts_created',0)}, errors={result.get('errors',0)}")
    except Exception as e:
        logger.warning(f"13F scan error: {e}")


async def _run_referral_hit_rewards():
    """Background: daily at 9:00 UTC — grant 7-day Pro to users whose share-ref
    crosses the monthly hit threshold."""
    try:
        from services.referral_rewards import scan_hit_threshold_rewards
        result = await scan_hit_threshold_rewards(db)
        logger.info(f"Referral hit rewards: granted={result.get('granted',0)}, skipped={result.get('skipped',0)}")
    except Exception as e:
        logger.warning(f"Referral hit rewards error: {e}")


async def _run_referral_monthly_rewards():
    """Background: 1st of each month at 9:30 UTC — grant tiered rewards to the
    top 5 sharers of the just-closed month."""
    try:
        from services.referral_rewards import scan_monthly_leaderboard_rewards
        result = await scan_monthly_leaderboard_rewards(db)
        logger.info(f"Referral monthly rewards: period={result.get('period')}, granted={result.get('count',0)}")
    except Exception as e:
        logger.warning(f"Referral monthly rewards error: {e}")


async def _run_help_search_digest():
    """Background: Mondays at 7:00 UTC — email admins a summary of the week's
    top zero-result Help Center searches (Feature-Gap Radar)."""
    try:
        from services.help_search_digest import send_help_search_digest
        result = await send_help_search_digest(db)
        logger.info(
            f"Help search digest: sent={result.get('sent', 0)}, "
            f"skipped={result.get('skipped')}, zero_events={result.get('zero_events')}, "
            f"top_gap={result.get('top_gap')}"
        )
    except Exception as e:
        logger.warning(f"Help search digest error: {e}")


async def _run_usaspending_warmup():
    """Background: 03:30 UTC — pre-resolve top federal recipients to tickers
    so the first gov-contracts dashboard load of the day is instant. Also
    catches new contractors as USASpending publishes them."""
    try:
        from services.usaspending_service import warmup_top_recipients
        result = await warmup_top_recipients(limit=500)
        logger.info(f"USASpending warmup: {result}")
    except Exception as e:
        logger.warning(f"USASpending warmup error: {e}")


async def _run_nightly_ml_retrain():
    """Background: 02:30 UTC — retrain the signal model on freshly labeled
    snapshots. Writes a new versioned artefact to /app/backend/models/ and
    appends a row to ml_training_log. Safe to run multiple times per day."""
    try:
        from services.ml_retrain_service import run_nightly_retrain
        result = await run_nightly_retrain(db)
        logger.info(
            f"Nightly ML retrain: status={result.get('status')} "
            f"samples={result.get('samples')} version={result.get('model_version')}"
        )
    except Exception as e:
        logger.warning(f"Nightly ML retrain error: {e}")


async def _run_self_test_monitor():
    """Background: every 15 minutes — run the self-test battery and append
    a one-liner to /app/memory/HEALTH_LOG.md, but only on state change or
    once per hour heartbeat so the log stays readable."""
    try:
        import pathlib
        from services.self_test_service import run_self_test

        # Best-effort: the scheduler reference is wired into the route
        # module; re-use it so job-registration check has something to see.
        scheduler_ref = None
        try:
            from routes.self_test import _scheduler as scheduler_ref  # noqa: F401
        except Exception:
            scheduler_ref = None

        report = await run_self_test(db, scheduler_ref)

        log_path = pathlib.Path("/app/memory/HEALTH_LOG.md")
        log_path.parent.mkdir(parents=True, exist_ok=True)

        # Find the previous overall status to decide if we need to log.
        last_overall = "UNKNOWN"
        if log_path.exists():
            for line in reversed(log_path.read_text().splitlines()):
                if "overall=" in line:
                    last_overall = line.split("overall=")[1].split()[0]
                    break

        now = datetime.now(timezone.utc)
        state_changed = report["overall"] != last_overall
        heartbeat = now.minute < 15  # hourly at the top of the hour

        if state_changed or heartbeat:
            line = (
                f"{report['timestamp']} "
                f"overall={report['overall']} "
                f"pass={report['passed']}/{report['total']} "
                f"fail={report['failed']}"
            )
            if report["failed"] > 0:
                fails = [c["name"] for c in report["checks"] if c["status"] == "FAIL"]
                line += f" failures=[{', '.join(fails)}]"
            if state_changed and last_overall != "UNKNOWN":
                line += f"  # state {last_overall} → {report['overall']}"
            with log_path.open("a") as f:
                f.write(line + "\n")

        if report["failed"] > 0:
            logger.warning(
                f"Self-test FAIL: {[c['name'] for c in report['checks'] if c['status']=='FAIL']}"
            )
    except Exception as e:
        logger.warning(f"Self-test monitor error: {e}")



def _start_cache_warmup():
    """Pre-populate expensive API caches so the first user never waits."""
    try:
        from services.cache import cache
        from services.sector_service import get_sector_heatmap
        from services.world_events_service import WorldEventsService
        from services.foreign_markets_service import ForeignMarketsService

        async def _warm():
            for key, fn, ttl in [
                ("sector_heatmap", get_sector_heatmap, 120),
                ("world_events", WorldEventsService().scrape_world_events, 300),
                ("foreign_markets", ForeignMarketsService().get_foreign_markets, 60),
            ]:
                try:
                    await cache.get_or_fetch(key, fn, ttl=ttl)
                    logger.info(f"Cache warm-up: {key} loaded")
                except Exception as e:
                    logger.warning(f"Cache warm-up {key} failed: {e}")

        import asyncio
        asyncio.create_task(_warm())
        logger.info("Cache warm-up started in background")

        async def _verify_loop():
            while True:
                await asyncio.sleep(3600)
                try:
                    from services.prediction_tracker import verify_pending_predictions
                    await verify_pending_predictions(db)
                    logger.info("Prediction verification cycle complete")
                except Exception as ve:
                    logger.warning(f"Prediction verification failed: {ve}")

        asyncio.create_task(_verify_loop())
        logger.info("Prediction verification scheduler started")
    except Exception as e:
        logger.warning(f"Cache warm-up setup failed: {e}")


def _write_test_credentials():
    """Write test credentials to memory file for testing agents."""
    creds_path = Path("/app/memory/test_credentials.md")
    creds_path.parent.mkdir(parents=True, exist_ok=True)
    creds_path.write_text(
        "# Test Credentials\n\n"
        "## Owner (RISEDUAL)\n"
        f"- Email: {os.environ.get('OWNER_EMAIL', 'managingdirector@redslateholdings.com')}\n"
        f"- Password: {os.environ.get('OWNER_PASSWORD', '')}\n"
        "- Role: owner\n- Subscription: pro\n"
        "- Can activate/deactivate users and grant/revoke Pro\n\n"
        f"## Admin\n- Email: {os.environ.get('ADMIN_EMAIL', 'admin@risedual.ai')}\n"
        f"- Password: {os.environ.get('ADMIN_PASSWORD', '')}\n"
        "- Role: admin\n- Subscription: pro\n\n"
        "## Auth Method\n- httpOnly secure cookies (primary)\n"
        "- POST /api/auth/login → sets access_token + refresh_token cookies\n"
        "- CORS: credentials: 'include' required on all fetch calls\n"
    )


@app.on_event("shutdown")
async def shutdown_db_client():
    try:
        from services.kraken_ws_stream import stop_kraken_ws_stream
        await stop_kraken_ws_stream()
    except Exception as e:  # noqa: BLE001
        logger.debug(f"Kraken WS stop on shutdown: {e}")
    client.close()
