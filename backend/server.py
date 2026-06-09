"""RISEDUAL AI — FastAPI Application Entry Point.

Thin orchestrator: connects MongoDB, registers route modules, handles startup/shutdown.
"""
from fastapi import FastAPI, APIRouter
from fastapi.responses import FileResponse
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
import asyncio
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


# Public v1 brain identity surface (consumed by MC's BrainHealthTile).
# MUST be registered BEFORE api_router so it wins the /api/status path
# match — the legacy api_router.get("/status") above returns the
# status_checks debug collection and would shadow MC's contract path.
from routes.public_status import router as public_status_router
app.include_router(public_status_router)


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

# Public-access lockout middleware — returns 503 to non-admin /api
# traffic when ``PUBLIC_ACCESS_ENABLED`` (or the runtime override)
# is false. Auth + health + system-access endpoints stay reachable.
# Registered AFTER CORS so 503 responses still carry CORS headers.
from services.public_access_middleware import PublicAccessMiddleware
app.add_middleware(PublicAccessMiddleware)

# Public-access route (GET state, POST flip — owner/admin only).
from routes.system_access import router as system_access_router
app.include_router(system_access_router, prefix="/api")

# Chevelle calibration governance (Patent J card + admin refit).
from routes.governance_chevelle_calibration import router as calibration_router
app.include_router(calibration_router)

# Crypto live trading status (owner-only diagnostic for the live
# Kraken wire — armed/config/open positions/daily count).
from routes.admin_crypto_live_status import router as crypto_live_status_router, set_db as _set_crypto_live_status_db
app.include_router(crypto_live_status_router)
_set_crypto_live_status_db(db)

# MC2 — in-process Mission Control surface (Phase A, 2026-06-09).
# Owner-only diagnostic endpoints + module-level db handle so MC2
# writers (intent / opinion / outcome) can persist to local Mongo
# collections when ``RISEDUAL_STANDALONE_MODE=1``.
from routes.admin_mc2 import router as mc2_router
from services.mc2 import set_db as _set_mc2_db
app.include_router(mc2_router)
_set_mc2_db(db)

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


async def _run_wedge_alerter_tick():
    """Heartbeat-driven wedge alerter — pages on frozen lanes /
    feature-health flood. Notification-only; no broker, no enforce.
    Always safe to schedule — no-ops gracefully when webhook is
    missing (logs OPS_ALERT_WEBHOOK_URL_MISSING once per tick)."""
    try:
        from services.wedge_alerter import run_tick
        result = await run_tick(db)
        if result.get("fresh_alerts"):
            logger.info(
                "[wedge-alerter] tick: fresh=%s suppressed=%s any_frozen=%s",
                [a.get("alert_key") for a in result.get("fresh_alerts") or []],
                [a.get("alert_key") for a in result.get("suppressed_cooldown") or []],
                result.get("any_frozen"),
            )
    except Exception as e:
        logger.warning(f"Wedge alerter tick failed: {e}")


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


def _alias_mc_identity_env_vars() -> None:
    """Alias Alpha's historical env-var names → MC v1 spec names.

    The drop-in ``sidecar/mc_identity_v1.py`` module from MC reads four
    env vars by exact name: ``MC_URL``, ``MC_INGEST_TOKEN``,
    ``MC_BASE_URL``, ``HEARTBEAT_TOKEN``. Alpha's deployment historically
    uses different names for the same values:

    +-----------------+-------------------------------+
    | MC v1 name      | Alpha historical name(s)      |
    +-----------------+-------------------------------+
    | MC_URL          | RISEDUAL_MC_URL               |
    | MC_INGEST_TOKEN | ALPHA_MC_INGEST_TOKEN /       |
    |                 | ALPHA_INGEST_TOKEN            |
    | MC_BASE_URL     | MC_BASE_URL (already correct) |
    | HEARTBEAT_TOKEN | MONOREPO_INGEST_TOKEN /       |
    |                 | ALPHA_INGEST_TOKEN            |
    +-----------------+-------------------------------+

    We copy values into the v1 names ONLY if the v1 name isn't already
    set — never overwrite an operator-set value. This keeps the spec
    module pristine (no Alpha-specific renames) and the v1 lifecycle
    log honest.
    """
    import os as _os

    def _ensure(target: str, sources: list[str]) -> None:
        if (_os.environ.get(target) or "").strip():
            return
        for src in sources:
            value = (_os.environ.get(src) or "").strip()
            if value:
                _os.environ[target] = value
                return

    _ensure("MC_URL", ["RISEDUAL_MC_URL", "MC_BASE_URL"])
    _ensure("MC_INGEST_TOKEN", ["ALPHA_MC_INGEST_TOKEN", "ALPHA_INGEST_TOKEN"])
    _ensure("MC_BASE_URL", ["RISEDUAL_MC_URL"])
    _ensure(
        "HEARTBEAT_TOKEN",
        ["MONOREPO_INGEST_TOKEN", "ALPHA_MC_INGEST_TOKEN", "ALPHA_INGEST_TOKEN"],
    )
    # v1 identity also reads ENV_NAME + BROKER_MODE for the chip header —
    # alias Alpha's existing names so the chip reflects environment
    # without forcing the operator to set two variables for the same thing.
    _ensure("ENV_NAME", ["RISEDUAL_ENV"])
    _ensure("BROKER_MODE", ["RISEDUAL_BROKER_MODE"])


@app.on_event("startup")
async def startup_event():
    logger.info("=== RISEDUAL AI STARTUP BEGIN ===")
    try:
        wire_db(db)
        app.state.db = db
        logger.info("DB wired successfully")
    except Exception as e:
        logger.exception(f"CRITICAL: DB wire failed: {e}")

    # MC Keys Proxy — pull canonical market-data keys (Polygon, Finnhub)
    # from MC's ``/api/admin/keys/market-data`` endpoint and stamp into
    # ``os.environ`` BEFORE any market-data service initialises. Local
    # ``.env`` values stay as the fallback if MC is unreachable. See
    # services/mc_keys_proxy.py for the doctrine pins.
    try:
        from services.mc_keys_proxy import fetch_and_apply as _fetch_mc_keys
        _mc_keys_result = _fetch_mc_keys()
        logger.info("[mc_keys_proxy] boot result: %s", _mc_keys_result)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"mc_keys_proxy wire-up failed (non-critical): {e}")

    # Phase 1 — Shelly Federation pipeline singleton.
    # Initialises the 5-Shelly federation (Alpha/Camaro/Chevelle/RedEye + MC)
    # and registers it on the MC emitter so any MC verifier site that
    # calls ``emit_mc_event(...)`` can route through Shelly-MC.
    # Fail-soft: a wiring failure logs and continues — Shelly is
    # observation-only and must never block boot.
    try:
        from shelly import ShellyPipeline
        from shelly.mc_emitter import set_pipeline as _set_shelly_pipeline
        from shelly.indexes import ensure_indexes as _shelly_ensure_indexes
        shelly_pipeline = ShellyPipeline(db)
        app.state.shelly_pipeline = shelly_pipeline
        _set_shelly_pipeline(shelly_pipeline)
        logger.info(
            "Shelly Federation wired: %d nodes (%s)",
            len(shelly_pipeline.locals),
            ", ".join(shelly_pipeline.locals.keys()),
        )
        try:
            idx_result = await _shelly_ensure_indexes(db)
            logger.info("Shelly Federation indexes ensured: %s", idx_result)
        except Exception as ie:  # noqa: BLE001
            logger.warning(
                "Shelly Federation index creation failed (non-critical): %s",
                ie,
            )
    except Exception as e:  # noqa: BLE001
        logger.warning("Shelly Federation wire-up failed (non-critical): %s", e)

    # MC Survival Layer check-in. Posts Alpha's RuntimeStamp to MC at
    # /api/admin/runtime/sidecar-checkin/alpha so the operator can see
    # who's prod vs preview live on Diagnostics. Observability only —
    # does NOT gate execution. The broker-receipt seal remains the
    # lock on bad orders. See services/mc_checkin/__init__.py.
    #
    # Preview-pod skip (2026-02-27): preview-tier pods MUST NOT POST
    # to a production MC. Same brain_id + ingest token can't be
    # disambiguated on MC's side and the preview stamp pollutes the
    # operator's diagnose view (the "two pods" duplicate-checkin bug
    # confirmed via pip_fingerprint divergence). Override with
    # RISEDUAL_MC_CHECKIN_ENABLE_ON_PREVIEW=1 only when explicitly
    # testing the checkin loop itself.
    #
    # v1 identity surface (2026-05-30): MC's BrainHealthTile reads
    # GET /api/status which calls build_identity_block(). That fn
    # reads the v1 env-var names (MC_URL / MC_INGEST_TOKEN /
    # MC_BASE_URL / HEARTBEAT_TOKEN). Alpha's deployment historically
    # uses different names — alias them here so the v1 module stays
    # pristine (drop-in from MC) and the tripwire log line is honest.
    _alias_mc_identity_env_vars()
    try:
        from sidecar.mc_identity_v1 import (
            build_identity_block as _v1_identity,
            log_lifecycle as _v1_lifecycle,
        )
        from routes.public_status import APP_NAME, SIDECAR_VERSION
        _identity_block = _v1_identity(
            app_name=APP_NAME, sidecar_version=SIDECAR_VERSION,
        )
        _v1_lifecycle(_identity_block)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"mc_identity_v1 lifecycle log failed (non-critical): {e}")
    try:
        from services.mc_checkin import (
            RuntimeStamp,
            checkin_now,
            start_periodic_checkin,
        )
        stamp = RuntimeStamp.current()
        app.state.runtime_stamp = stamp
        logger.info(
            "alpha runtime stamp: env=%s platform=%s git=%s policy_hash=%s "
            "broker_mode=%s local_execution_authority=%s",
            stamp.env_name, stamp.platform, stamp.git_sha,
            stamp.policy_hash[:8], stamp.broker_mode,
            stamp.local_execution_authority,
        )
        _preview_override = os.environ.get(
            "RISEDUAL_MC_CHECKIN_ENABLE_ON_PREVIEW", "",
        ).strip().lower() in ("1", "true", "yes", "on")
        if stamp.env_name != "prod" and not _preview_override:
            logger.info(
                "[mc_checkin] SKIPPED — env_name=%r is not 'prod'. "
                "Preview/dev pods do not post to MC. Set "
                "RISEDUAL_MC_CHECKIN_ENABLE_ON_PREVIEW=1 to override.",
                stamp.env_name,
            )
        else:
            try:
                await checkin_now()
            except Exception as e:  # noqa: BLE001
                # Don't block boot on MC being flaky — but log loudly.
                logger.warning(f"mc_checkin boot ping failed (non-critical): {e}")
            start_periodic_checkin(app.state)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"mc_checkin wire-up failed (non-critical): {e}")

    try:
        await _start_schedulers()
        logger.info("Schedulers started")
    except Exception as e:
        logger.warning(f"Scheduler startup failed (non-critical): {e}")
        # Surface this to the Health panel so the operator can read
        # the actual failure cause without grepping pod logs. Boot
        # incident 2026-05-06: handle stayed None across redeploys
        # and the only evidence was this swallowed warning.
        try:
            from routes.self_test import set_scheduler_boot_error
            set_scheduler_boot_error(e, phase="startup_event")
        except Exception:  # noqa: BLE001
            pass

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

    # Patent M Phase 3 — rehydrate the learning-core resolved-memory
    # bank from Mongo so cluster centroids survive process restarts.
    # Gated on ``LEARNING_CORE_REHYDRATE_ON_STARTUP`` (default off).
    # Best-effort: a Mongo failure logs a warning and leaves the
    # core running cold. Never raises.
    try:
        from services.learning_core_service import rehydrate_core_from_mongo
        result = await rehydrate_core_from_mongo(db)
        if result.get("replayed", 0) > 0:
            logger.info(
                "[learning-core] rehydrated %d/%d resolved memories",
                result["replayed"], result["loaded"],
            )
        elif "skipped" in result:
            logger.info(
                "[learning-core] rehydrate skipped: %s", result["skipped"],
            )
    except Exception as e:
        logger.warning(
            f"Learning-core rehydrate failed (non-critical): {e}"
        )

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

    # Alpha Monorepo Sidecar — register active calibrator artifact +
    # spawn the heartbeat loop. Fire-and-forget; the monorepo being
    # down must never affect this runtime. Gated on env presence.
    try:
        from services import risedual_monorepo_client as _mono
        if _mono._enabled():
            asyncio.create_task(_monorepo_register_artifacts_at_startup())
            global _monorepo_heartbeat_task  # noqa: PLW0603
            _monorepo_heartbeat_task = asyncio.create_task(
                _monorepo_heartbeat_loop()
            )
            logger.info("[monorepo] sidecar enabled — heartbeat loop spawned")
            # ── MC INBOX POLLER (Doctrine V3, 2026-05-14) ──
            # Inbound half of the sidecar: pulls cross-brain opinions,
            # roles manifest, and our scorecard from MC and routes each
            # opinion through Shelly's perception layer. Never raises.
            try:
                from services.mc_inbox_poller import run_forever as _mc_inbox_run
                global _mc_inbox_task  # noqa: PLW0603
                _mc_inbox_task = asyncio.create_task(_mc_inbox_run(db))
                logger.info("[mc_inbox] poller spawned (opinions/roles/scorecard)")
            except Exception as e:
                logger.warning(f"[mc_inbox] poller startup failed (non-critical): {e}")
        else:
            logger.info("[monorepo] sidecar disabled (env not set)")
    except Exception as e:
        logger.warning(f"[monorepo] sidecar startup skipped (non-critical): {e}")

    # ── In-process MC sidecar (alternative to the supervisor-managed
    # external sidecar). Gated by ALPHA_INPROCESS_SIDECAR=1. Disabled
    # by default so the existing external sidecar continues to own
    # the wire — flip the flag (and stop the supervisor program) to
    # switch. See services/mc_sidecar.py for the operator runbook.
    try:
        if os.environ.get("ALPHA_INPROCESS_SIDECAR", "0") == "1":
            from services import mc_sidecar as _mc_sidecar
            await _mc_sidecar.start(db)
            logger.info(
                "[mc_sidecar] in-process sidecar started "
                "(heartbeat/contribution/watchdog asyncio tasks)",
            )
        else:
            logger.info("[mc_sidecar] in-process sidecar disabled (ALPHA_INPROCESS_SIDECAR != 1)")
    except Exception as e:
        logger.warning(f"[mc_sidecar] in-process startup skipped (non-critical): {e}")

    # ── In-process Sovereign Sidecar (2026-02-23 prod-deploy fix).
    # Emergent's deploy image doesn't ship the supervisor's
    # ``alpha-sidecar.conf``, so prod has been running with NO
    # Sovereign contribution loop. This in-process variant uses the
    # SAME ``SovereignSidecar`` class as the supervisor program and
    # is lockfile-guarded against double-firing in preview (where the
    # supervisor process IS running). Default OFF; operator flips
    # ``ALPHA_INPROCESS_SIDECAR_ENABLED=1`` in prod env after deploy.
    try:
        from sovereign import inprocess_sidecar as _alpha_sov
        ips_status = await _alpha_sov.start()
        logger.info(
            "[alpha_inprocess_sidecar] startup: %s (%s)",
            "started" if ips_status.get("started") else "skipped",
            ips_status.get("reason"),
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            f"[alpha_inprocess_sidecar] startup skipped (non-critical): {e}"
        )

    logger.info(f"=== RISEDUAL AI STARTUP COMPLETE — {len(app.routes)} routes registered ===")


_monorepo_heartbeat_task: asyncio.Task | None = None
_mc_inbox_task: asyncio.Task | None = None


async def _monorepo_register_artifacts_at_startup():
    """Best-effort one-shot: register the currently-active calibrator
    with the monorepo. Never raises."""
    try:
        from services import risedual_monorepo_client as _mono
        from services import calibration_layer as _cal
        version = _cal._get_active_version()
        if not version:
            return
        path = _cal._artifact_path(version)
        if not path.exists():
            return
        import hashlib as _hashlib
        try:
            sha = _hashlib.sha256(path.read_bytes()).hexdigest()
        except Exception:  # noqa: BLE001
            sha = ""
        await _mono.register_calibrator(
            name="chevelle_isotonic",
            version=version,
            method="isotonic",
        )
        await _mono.register_artifact(
            artifact="calibrator",
            version=version,
            sha=sha,
        )
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[monorepo] artifact registration skipped: {e}")


async def _monorepo_heartbeat_loop():
    """Liveness ping every 60s. Runs forever until the task is
    cancelled at shutdown. Never raises out of the loop."""
    try:
        from services import risedual_monorepo_client as _mono
        while True:
            try:
                await _mono.heartbeat(status="ok")
            except Exception as e:  # noqa: BLE001
                logger.debug(f"[monorepo] heartbeat failed: {e}")
            await asyncio.sleep(60)
    except asyncio.CancelledError:
        pass
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[monorepo] heartbeat loop exited: {e}")


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
                # NOTE: do NOT re-import ``datetime``/``timezone`` here.
                # They're already imported at the module level (line 15).
                # A redundant ``from datetime import datetime, timezone``
                # inside this conditional block makes ``datetime`` a
                # FUNCTION-LOCAL name for the entire ~550-line
                # ``_start_schedulers`` body — which means if this
                # ``if`` branch is skipped (no owner, or seed disabled),
                # the local stays unbound, and 500 lines later the
                # ``next_run_time=datetime.now(...)`` call raises
                # ``UnboundLocalError`` and the scheduler never starts.
                # Production incident 2026-05-06: scheduler stalled
                # 3 days because of exactly this shadow.
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
        from services.scheduling import register_all as _register_scheduler_jobs
        # 2026-06-03 hardening: explicit job_defaults so stalled
        # ticks DON'T stack up and starve the event loop. Observed
        # 8h pod silence on 2026-06-03 (06:11→14:14 UTC) caused by
        # _check_smart_orders instances piling up behind hung httpx
        # sockets. Defaults below:
        #   * coalesce=True: if N misfires queued, run ONE catch-up
        #     run instead of N back-to-back replays.
        #   * misfire_grace_time=60: any tick that's more than 60s
        #     late is dropped, not deferred. Keeps the queue
        #     bounded.
        #   * max_instances=2: a single hung tick can't permanently
        #     block the next one (still bounded — we don't want
        #     unbounded fan-out).
        scheduler = AsyncIOScheduler(job_defaults={
            "coalesce": True,
            "misfire_grace_time": 60,
            "max_instances": 2,
        })
        # Job registration was strangler-split out of this function
        # on 2026-05-08 (Architecture Split Step 3). Every add_job
        # call — same IDs, same intervals, same replace_existing
        # flags — now lives in services/scheduling/jobs.py.
        # ``server`` (this module) is passed in as ``server_mod`` so
        # job registration can reach the module-level callbacks
        # (_check_smart_orders, _run_grid_bots, ...) without a
        # circular import at load time.
        import sys as _sys
        _register_scheduler_jobs(scheduler, db, _sys.modules[__name__])

        scheduler.start()
        # Expose the started scheduler to the self-test route so its
        # /api/admin/self-test probe can check job registration health.
        try:
            from routes.self_test import set_scheduler as _set_self_test_scheduler
            _set_self_test_scheduler(scheduler)
        except Exception as e:
            logger.warning(f"Self-test scheduler wire failed: {e}")
        logger.info("Schedulers started: digest (6:00), watchlist (5:30), memory cleanup (2:00), nightly ML retrain (2:30), waitlist invite (9:00), smart orders (30s), grid bots (30s), signal dispatcher (5m), headlines (15m), predictions (10m), ML labeler (1h), FRED snapshot (7:00), 13F scan (8:00), referral hit rewards (9:00 daily), referral monthly rewards (1st @ 9:30), help search digest (Mon 7:00), USASpending warmup (3:30), self-test monitor (15m), conviction drift (8:00), tier3 digest (8:15), ML health digest (8:00), paper-trade closer (60m), tier3 paper closer (15m), crypto paper bot (15m, 24/7), crypto closer (15m, 12h hold), crypto adaptation detector (6h), position reconciler (30m), alpaca position closer (5m), drift alert watcher (5m), top-universe rebuild (Sun 00:00), top-universe warm post-close (21:05), top-universe warm pre-open (13:00), options-universe warm (5m, market-hours-gated), notification lifecycle sweep (4:00 + 16:00)")
    except Exception as e:
        logger.warning(f"Scheduler setup failed: {e}")
        # Pump the traceback to the Health panel — the outer
        # startup_event handler only catches if THIS handler
        # re-raises, so we need to record it ourselves here too.
        try:
            from routes.self_test import set_scheduler_boot_error
            set_scheduler_boot_error(e, phase="_start_schedulers")
        except Exception:  # noqa: BLE001
            pass


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


async def _run_alpaca_position_closer():
    """Background (5m): live Alpaca position exit engine.

    Closes brain-opened Alpaca positions when SL / TP / trail / max-
    hold / pre-expiry trigger. Default OFF — master switch
    ``ALPACA_POSITION_CLOSER_ENABLED``. Default dry-run when enabled
    (``ALPACA_POSITION_CLOSER_DRY_RUN=true``) so the operator can
    audit the exit tape against live positions before going hot.
    """
    try:
        from services.alpaca_position_closer import (
            close_due_alpaca_positions,
        )
        result = await close_due_alpaca_positions(db)
        if result.get("closed") or result.get("errors"):
            logger.info(
                "Alpaca position closer: %s closed=%d errors=%d "
                "evaluated=%d reasons=%s",
                "DRY-RUN" if result.get("dry_run") else "LIVE",
                result.get("closed", 0),
                result.get("errors", 0),
                result.get("evaluated", 0),
                result.get("reasons", {}),
            )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Alpaca position closer tick failed: {e}")


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


async def _run_crypto_live_closer():
    """Background: 5-minute live crypto closer + orphan-leg
    canceller. Cancels the resting SL or TP leg whenever one
    bracket leg fires on Kraken, and patches the corresponding
    ``crypto_live_trades`` row to ``status="closed"``. No-op when
    ``RISEDUAL_CRYPTO_LIVE_EXEC`` is unset.
    """
    try:
        from services.crypto_live_closer import run_crypto_live_closer_pass
        summary = await run_crypto_live_closer_pass(db)
        if summary.get("sl_hit") or summary.get("tp_hit") or summary.get("errors"):
            logger.info(
                "Crypto live closer: scanned=%d sl_hit=%d tp_hit=%d skipped=%d errors=%d",
                summary.get("scanned", 0),
                summary.get("sl_hit", 0),
                summary.get("tp_hit", 0),
                summary.get("skipped", 0),
                summary.get("errors", 0),
            )
    except Exception as e:
        logger.debug(f"Crypto live closer error: {e}")


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


async def _run_tier3_paper_closer():
    """Background: Every 15 min. Close Tier-3 Accumulator bot fills
    that the legacy `paper_trade_closer` skips (Schema-B rows
    without a `status` field). Mirrors the crypto closer's exit
    cascade — SL → TP (off by default) → trailing-stop → max_hold.

    Plugs the 198:8 BUY:SELL ratio observed in 2026-Q2 — Tier-3
    bots were buying without anything ever closing.
    """
    try:
        from services.tier3_paper_closer import close_due_tier3_paper_trades
        result = await close_due_tier3_paper_trades(db)
        if result.get("closed") or result.get("errors"):
            logger.info(
                "Tier3 paper closer: evaluated=%d closed=%d errors=%d reasons=%s",
                result.get("positions_evaluated", 0),
                result.get("closed", 0),
                result.get("errors", 0),
                result.get("reasons", {}),
            )
    except Exception as e:
        logger.debug(f"Tier3 paper closer error: {e}")



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
            # 2026-05-22: cadence dropped 1h → 5min after diagnosing a
            # 2,000+ row verification backlog blocking Tier 3 readiness
            # (high-conf samples couldn't refill the rolling 30-day
            # window because the verifier drained <20 rows/hour while
            # signal_dispatcher emitted hundreds/hour).
            while True:
                await asyncio.sleep(300)
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
    # Cancel monorepo heartbeat + close the httpx client.
    try:
        global _monorepo_heartbeat_task  # noqa: PLW0603
        if _monorepo_heartbeat_task is not None:
            _monorepo_heartbeat_task.cancel()
        from services.risedual_monorepo_client import aclose as _mono_aclose
        await _mono_aclose()
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[monorepo] shutdown cleanup: {e}")
    # In-process MC sidecar — cancel the three asyncio loops cleanly.
    try:
        if os.environ.get("ALPHA_INPROCESS_SIDECAR", "0") == "1":
            from services import mc_sidecar as _mc_sidecar
            await _mc_sidecar.stop(db)
            logger.info("[mc_sidecar] in-process sidecar stopped on shutdown")
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[mc_sidecar] shutdown cleanup: {e}")
    # In-process Sovereign sidecar (2026-02-23 prod-deploy fix) —
    # cancel the contribution loop cleanly so the shutdown doesn't
    # log a "pending task" warning.
    try:
        from sovereign import inprocess_sidecar as _alpha_sov
        await _alpha_sov.stop()
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[alpha_inprocess_sidecar] shutdown cleanup: {e}")
    # MC check-in periodic loop — cancel cleanly to avoid an asyncio
    # warning about a pending task on shutdown.
    try:
        from services.mc_checkin import stop_periodic_checkin
        await stop_periodic_checkin()
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[mc_checkin] shutdown cleanup: {e}")
    client.close()
