"""Admin-only routes: cache monitoring, system diagnostics, broker OAuth config."""
from fastapi import APIRouter, HTTPException, Request
from datetime import datetime, timezone
import logging

router = APIRouter(prefix="/api/admin", tags=["admin"])
logger = logging.getLogger(__name__)

db = None

def set_db(database):
    global db
    db = database


async def _require_admin(request: Request):
    from routes.auth import get_current_user
    user = await get_current_user(request)
    if user.get("role") not in ("owner", "admin"):
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


async def _require_owner(request: Request):
    """Stricter gate than `_require_admin`: only the owner role passes.
    Used for endpoints that expose upstream-provider load signals, which
    are business-sensitive (reveals API budget pressure, traffic patterns,
    which symbols are hottest) and shouldn't be visible to lower-tier
    admins or any public caller.
    """
    from routes.auth import get_current_user
    user = await get_current_user(request)
    if user.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Owner access required")
    return user


# ============================================================
# CACHE MANAGEMENT
# ============================================================

@router.get("/cache-stats")
async def get_cache_stats(request: Request):
    await _require_admin(request)
    from services.cache import cache
    return cache.stats()


@router.post("/cache-invalidate/{key}")
async def invalidate_cache_key(key: str, request: Request):
    await _require_admin(request)
    from services.cache import cache
    cache.invalidate(key)
    return {"ok": True, "invalidated": key}


@router.post("/cache-clear")
async def clear_all_cache(request: Request):
    await _require_admin(request)
    from services.cache import cache
    cache.clear()
    return {"ok": True, "message": "All cache cleared"}


@router.get("/price-cache-stats")
async def get_price_cache_stats(request: Request):
    """Stats on the sliding-TTL price cache shared by all price_provider
    entrypoints. Owner-only — exposes upstream-provider load patterns.

    - `size` / `alive`: total entries tracked vs. currently valid
    - `at_reset_cap`: entries that have hit `max_resets` — they still
      serve reads but won't extend expiry any more. If this climbs
      toward `alive` under load, upstream providers are about to be hit
      by a wave of refresh fetches — good leading indicator.
    - `ttl_seconds`, `max_resets`: effective cache policy
    """
    await _require_owner(request)
    from services.sliding_cache import price_cache
    return price_cache.stats()


@router.post("/price-cache-invalidate/{symbol}")
async def invalidate_price_cache(symbol: str, request: Request):
    """Force a fresh upstream fetch on the next read of `symbol`. Owner-only.
    Invalidates both quote and crypto keys since we don't know which applies."""
    await _require_owner(request)
    from services.sliding_cache import price_cache
    upper = symbol.upper()
    for key in (f"quote_{upper}", f"crypto_{upper}"):
        price_cache.invalidate(key)
    return {"ok": True, "invalidated": upper}


@router.get("/quiver-status")
async def quiver_status(request: Request):
    """Per-endpoint health + circuit-breaker state for QuiverQuant. Owner-only.

    Use this when gov filings / congress trading data looks thin — it tells
    you whether we're looking at a Quiver outage (circuit open) or just a
    quiet news day. Also shows cache pressure.
    """
    await _require_owner(request)
    from services.quiver_service import get_endpoint_health
    return get_endpoint_health()


@router.post("/usaspending-warmup")
async def usaspending_warmup(request: Request, limit: int = 500):
    """Manually trigger the USASpending.gov recipient→ticker warm-up job.
    Owner-only. Pre-resolves the top federal contractors so the first
    gov-contracts dashboard load is instant.

    Scheduled to run nightly at 03:30 UTC automatically — this endpoint
    is for on-demand re-runs (e.g. after clearing the cache, after a
    new contract cycle, or as a smoke test during ops review).
    """
    await _require_owner(request)
    from services.usaspending_service import warmup_top_recipients
    return await warmup_top_recipients(limit=min(max(limit, 10), 500))


@router.get("/usaspending-health")
async def usaspending_health(request: Request):
    """Cache + config snapshot for USASpending service. Owner-only."""
    await _require_owner(request)
    from services.usaspending_service import get_health
    return get_health()


@router.post("/retrain-now")
async def retrain_now(request: Request, max_samples: int = 50000):
    """Manually trigger a nightly ML retrain. Owner-only.

    Use this after a big data ingestion, when rolling forward from a bad
    model version, or simply to validate the pipeline end-to-end. Writes
    a new versioned artefact like the scheduled run — does NOT overwrite
    any prior model.
    """
    await _require_owner(request)
    from services.ml_retrain_service import run_nightly_retrain
    return await run_nightly_retrain(db, max_samples=max(200, min(max_samples, 200000)))


@router.post("/label-now")
async def label_now(request: Request):
    """Force a labeler run. Owner-only. Normally hourly via APScheduler."""
    await _require_owner(request)
    from services.ml_retrain_service import run_backfill_labeling
    return await run_backfill_labeling(db)


@router.get("/ml-training-history")
async def ml_training_history(request: Request, limit: int = 20):
    """Recent ML retrain runs with sample counts, versions, and errors.
    Owner-only. Pair with `GET /ml-latest-model` to see what's currently
    deployed."""
    await _require_owner(request)
    from services.ml_retrain_service import get_training_history
    runs = await get_training_history(db, limit=limit)
    return {"runs": runs, "count": len(runs)}


@router.get("/ml-latest-model")
async def ml_latest_model(request: Request):
    """Report the newest on-disk signal model artefact. Owner-only."""
    await _require_owner(request)
    from services.ml_retrain_service import get_latest_model_info
    info = get_latest_model_info()
    return info or {"version": None, "message": "No trained model artefacts on disk"}


# ============================================================
# BROKER OAUTH CONFIGURATION (Owner only)
# ============================================================

@router.get("/broker-oauth")
async def get_broker_oauth_config(request: Request):
    """Get OAuth configuration status for all supported brokers. Owner only."""
    user = await _require_admin(request)
    if user.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Owner access required")

    configs = {}
    cursor = db.broker_oauth_config.find({}, {"_id": 0, "client_secret_enc": 0})
    async for doc in cursor:
        configs[doc["broker_id"]] = {
            "broker_id": doc["broker_id"],
            "configured": True,
            "client_id_preview": doc.get("client_id_preview", ""),
            "updated_at": doc.get("updated_at", ""),
            "updated_by": doc.get("updated_by", ""),
        }

    # Include supported brokers even if not configured
    from routes.broker import OAUTH_CONFIGS
    for broker_id in OAUTH_CONFIGS:
        if broker_id not in configs:
            configs[broker_id] = {
                "broker_id": broker_id,
                "configured": False,
                "client_id_preview": "",
            }

    return {"brokers": configs}


@router.post("/broker-oauth/{broker_id}")
async def set_broker_oauth_config(broker_id: str, request: Request):
    """Set or update OAuth Client ID and Secret for a broker. Owner only.

    Body: {"client_id": "...", "client_secret": "..."}
    """
    user = await _require_admin(request)
    if user.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Owner access required")

    from routes.broker import OAUTH_CONFIGS, encrypt_value
    if broker_id not in OAUTH_CONFIGS:
        raise HTTPException(status_code=400, detail=f"Broker '{broker_id}' does not support OAuth")

    body = await request.json()
    client_id = body.get("client_id", "").strip()
    client_secret = body.get("client_secret", "").strip()

    if not client_id or not client_secret:
        raise HTTPException(status_code=400, detail="Both client_id and client_secret are required")

    # Store encrypted secret, plaintext ID (needed for OAuth redirects)
    doc = {
        "broker_id": broker_id,
        "client_id": client_id,
        "client_id_preview": f"{client_id[:8]}...{client_id[-4:]}" if len(client_id) > 12 else client_id[:4] + "...",
        "client_secret_enc": encrypt_value(client_secret),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "updated_by": user.get("email", "unknown"),
    }

    await db.broker_oauth_config.update_one(
        {"broker_id": broker_id},
        {"$set": doc},
        upsert=True,
    )

    logger.info(f"Broker OAuth config updated for {broker_id} by {user.get('email')}")
    return {
        "status": "configured",
        "broker_id": broker_id,
        "client_id_preview": doc["client_id_preview"],
        "message": f"OAuth credentials saved for {broker_id}",
    }


@router.delete("/broker-oauth/{broker_id}")
async def delete_broker_oauth_config(broker_id: str, request: Request):
    """Remove OAuth configuration for a broker. Owner only."""
    user = await _require_admin(request)
    if user.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Owner access required")

    result = await db.broker_oauth_config.delete_one({"broker_id": broker_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="No OAuth config found for this broker")

    return {"status": "deleted", "broker_id": broker_id}


# ── Codebase Download ──

@router.get("/download/codebase-txt")
async def download_codebase_txt(request: Request):
    """Download the entire codebase as a single .txt file."""
    await _require_admin(request)
    import os
    from fastapi.responses import Response

    EXTENSIONS = {'.py', '.js', '.jsx', '.ts', '.tsx', '.css', '.html', '.json', '.md', '.txt', '.yml', '.yaml', '.toml', '.cfg'}
    SKIP_DIRS = {'node_modules', '__pycache__', '.git', '.emergent', 'chromadb', 'dist', 'build', '.next', 'venv', '.venv'}
    SKIP_FILES = {'.env', '.env.test', '.env.local', '.env.production'}
    MAX_FILE_SIZE = 200_000  # skip files > 200KB

    lines = []
    lines.append("=" * 80)
    lines.append("RISEDUAL AI — Complete Source Code Export")
    lines.append(f"Generated: {datetime.now(timezone.utc).isoformat()}")
    lines.append("=" * 80)
    lines.append("")

    file_count = 0
    for root, dirs, files in os.walk("/app"):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fname in sorted(files):
            ext = os.path.splitext(fname)[1].lower()
            if ext not in EXTENSIONS or fname in SKIP_FILES:
                continue
            fpath = os.path.join(root, fname)
            rel = os.path.relpath(fpath, "/app")
            try:
                size = os.path.getsize(fpath)
                if size > MAX_FILE_SIZE:
                    lines.append(f"\n{'─' * 80}")
                    lines.append(f"FILE: {rel}  [SKIPPED — {size:,} bytes]")
                    continue
                with open(fpath, "r", errors="replace") as f:
                    content = f.read()
                lines.append(f"\n{'─' * 80}")
                lines.append(f"FILE: {rel}  ({len(content.splitlines())} lines)")
                lines.append("─" * 80)
                lines.append(content)
                file_count += 1
            except Exception:
                pass

    lines.insert(4, f"Files: {file_count}")
    body = "\n".join(lines)

    return Response(
        content=body.encode("utf-8"),
        media_type="text/plain",
        headers={"Content-Disposition": "attachment; filename=RISEDUAL_AI_Codebase.txt"},
    )
