"""Media upload/download routes — admin media manager for videos, images, etc.

SEC-001 hardening (2026-02): upload/list/delete endpoints now require admin
authentication (owner/admin role), ``uploaded_by`` is derived from the
authenticated user's email/id (never hardcoded), and chunked staging is
bounded by explicit per-chunk and per-upload caps to prevent memory-exhaustion
DoS from unauthenticated callers.

The two public read paths remain unauthenticated on purpose:
* ``GET /api/media/landing-video`` — used by the pre-login landing page.
* ``GET /api/media/file/{file_id}`` — public streaming path for embedded
  media.
"""
import re
import logging
import threading
import time
from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Response, Request
from services.storage_service import MediaService, get_object
from services.auth_helpers import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["media"])


# Upload IDs are interpolated into an in-memory dict key. We still
# require strictly safe characters to avoid weird IDs polluting logs
# and to keep the shape compatible with the previous filesystem layout.
# UUIDs (32 hex + 4 hyphens) and the legacy
# ``upload_<timestamp>_<rand>`` shape both pass.
_UPLOAD_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,128}$")

# In-memory chunk staging. Chunked uploads used to stage in
# ``/tmp/uploads/`` on the app pod, which is invisible across replicas
# and gets wiped on deploy. We now keep partial uploads entirely in
# process memory and only hand the assembled bytes to the durable
# object-storage service. Single-worker uvicorn keeps this safe today;
# if we ever scale horizontally the staging store should move to Redis.
_CHUNK_STAGE: dict[str, dict] = {}
_CHUNK_STAGE_LOCK = threading.Lock()
# Anything older than this without a completing chunk is garbage-
# collected so a stalled client can't pin memory forever.
_CHUNK_STAGE_TTL_SECS = 60 * 30

# Explicit memory-DoS caps. Individual chunks and the aggregated staging
# buffer are bounded independently so that a single call cannot pin
# unbounded memory even before assembly hits the storage-service cap.
_MAX_CHUNK_BYTES = 8 * 1024 * 1024      # 8 MiB per chunk
_MAX_UPLOAD_BYTES = 100 * 1024 * 1024   # 100 MiB total per upload_id


async def _require_admin(request: Request) -> dict:
    """Owner/admin gate. Mirrors ``admin_alpha_daytrader._require_admin``."""
    user = await get_current_user(request)
    if not user or user.get("role") not in ("owner", "admin"):
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


def _uploader_label(user: dict) -> str:
    """Prefer email; fall back to id; never the caller-supplied hardcode."""
    return str(user.get("email") or user.get("id") or user.get("user_id") or "admin")


def _validate_upload_id(upload_id: str) -> str:
    """Reject weird or oversized upload ids.

    Returns the validated id on success, raises 400 on failure. The
    error message is intentionally generic — we don't tell an
    attacker which character tripped the regex.
    """
    if not upload_id or not _UPLOAD_ID_RE.fullmatch(upload_id):
        raise HTTPException(
            status_code=400,
            detail="upload_id must be 8-128 chars of [A-Za-z0-9_-]",
        )
    return upload_id


def _gc_stage_locked() -> None:
    """Drop chunk-stage entries that have gone stale.

    Caller holds ``_CHUNK_STAGE_LOCK``.
    """
    now = time.time()
    stale = [
        uid
        for uid, entry in _CHUNK_STAGE.items()
        if now - entry.get("updated_at", 0) > _CHUNK_STAGE_TTL_SECS
    ]
    for uid in stale:
        _CHUNK_STAGE.pop(uid, None)


@router.post("/media/upload")
async def upload_media(
    request: Request,
    file: UploadFile = File(...),
    category: str = Form("general"),
):
    """Upload a media file (video, image, audio). Max 100MB. Admin only."""
    user = await _require_admin(request)
    try:
        data = await file.read()
        if len(data) < 100:
            raise HTTPException(status_code=400, detail="File is empty or too small")
        if len(data) > _MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"file exceeds {_MAX_UPLOAD_BYTES // 1024 // 1024}MB cap",
            )

        svc = MediaService()
        uploader = _uploader_label(user)
        result = await svc.upload(
            filename=file.filename or "upload.bin",
            data=data,
            content_type=file.content_type or "application/octet-stream",
            uploaded_by=uploader,
            category=category,
        )
        # Echo the derived uploader so clients can confirm attribution
        # without a follow-up GET /api/media round-trip.
        return {**result, "uploaded_by": uploader}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Upload error: {e}")
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")


@router.post("/media/upload-chunk")
async def upload_chunk(
    request: Request,
    file: UploadFile = File(...),
    chunk_index: int = Form(0),
    total_chunks: int = Form(1),
    upload_id: str = Form(""),
    filename: str = Form("upload.bin"),
    category: str = Form("general"),
    content_type: str = Form("application/octet-stream"),
):
    """Upload a file in chunks for large files. Assembles on last chunk.
    Admin only."""
    user = await _require_admin(request)
    # Validate FIRST — never let a malicious upload_id become a dict key.
    upload_id = _validate_upload_id(upload_id)

    chunk_data = await file.read()

    # Reject oversized single chunks before they hit the staging dict.
    if len(chunk_data) > _MAX_CHUNK_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"chunk exceeds {_MAX_CHUNK_BYTES // 1024 // 1024}MB cap",
        )

    # Stage the chunk entirely in memory. No pod-local staging file.
    with _CHUNK_STAGE_LOCK:
        _gc_stage_locked()
        entry = _CHUNK_STAGE.get(upload_id)
        if entry is None:
            entry = {"chunks": {}, "total_bytes": 0, "updated_at": time.time()}
            _CHUNK_STAGE[upload_id] = entry

        # Compute delta so replacing a chunk doesn't double-count against
        # the total cap.
        existing = entry["chunks"].get(chunk_index)
        delta = len(chunk_data) - (len(existing) if existing else 0)
        if entry["total_bytes"] + delta > _MAX_UPLOAD_BYTES:
            _CHUNK_STAGE.pop(upload_id, None)
            raise HTTPException(
                status_code=413,
                detail=f"upload exceeds {_MAX_UPLOAD_BYTES // 1024 // 1024}MB cap",
            )
        entry["chunks"][chunk_index] = chunk_data
        entry["total_bytes"] += delta
        entry["updated_at"] = time.time()
        received = len(entry["chunks"])

    logger.info(f"Chunk {chunk_index + 1}/{total_chunks} received for {upload_id}")

    # If this is the last chunk, assemble and upload
    if received >= total_chunks:
        try:
            with _CHUNK_STAGE_LOCK:
                entry = _CHUNK_STAGE.pop(upload_id, None)
            if entry is None:
                raise HTTPException(status_code=500, detail="chunk stage disappeared")

            assembled = bytearray()
            for i in range(total_chunks):
                part = entry["chunks"].get(i)
                if part is None:
                    raise HTTPException(
                        status_code=400,
                        detail=f"missing chunk {i} of {total_chunks}",
                    )
                assembled.extend(part)

            svc = MediaService()
            uploader = _uploader_label(user)
            result = await svc.upload(
                filename=filename,
                data=bytes(assembled),
                content_type=content_type,
                uploaded_by=uploader,
                category=category,
            )
            return {**result, "uploaded_by": uploader, "complete": True}
        except HTTPException:
            raise
        except Exception as e:
            with _CHUNK_STAGE_LOCK:
                _CHUNK_STAGE.pop(upload_id, None)
            logger.error(f"Chunk assembly error: {e}")
            raise HTTPException(status_code=500, detail=f"Assembly failed: {str(e)}")

    return {"chunk": chunk_index, "total": total_chunks, "complete": False}


@router.get("/media")
async def list_media(request: Request, category: str = None):
    """List all uploaded media files. Admin only."""
    await _require_admin(request)
    svc = MediaService()
    files = await svc.list_media(category)
    return {"files": files, "count": len(files)}


@router.get("/media/landing-video")
async def get_landing_video():
    """Get the latest landing page video (public, no auth)."""
    svc = MediaService()
    record = await svc.get_landing_video()
    if not record:
        return {"has_video": False}
    return {
        "has_video": True,
        "file_id": record["file_id"],
        "filename": record["original_filename"],
        "content_type": record["content_type"],
        "size": record.get("size", 0),
    }


@router.get("/media/file/{file_id}")
async def download_media(file_id: str):
    """Download/stream a media file by ID."""
    svc = MediaService()
    record = await svc.get_file_record(file_id)
    if not record:
        raise HTTPException(status_code=404, detail="File not found")

    try:
        data, ct = get_object(record["storage_path"])
        return Response(
            content=data,
            media_type=record.get("content_type", ct),
            headers={
                "Content-Disposition": f'inline; filename="{record["original_filename"]}"',
                "Cache-Control": "public, max-age=86400",
            },
        )
    except Exception as e:
        logger.error(f"Download error for {file_id}: {e}")
        raise HTTPException(status_code=500, detail="File download failed")


@router.delete("/media/{file_id}")
async def delete_media(request: Request, file_id: str):
    """Soft-delete a media file. Admin only."""
    await _require_admin(request)
    svc = MediaService()
    deleted = await svc.delete_file(file_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="File not found")
    return {"deleted": True, "file_id": file_id}
