"""Media upload/download routes — admin media manager for videos, images, etc."""
import os
import re
import logging
from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Response
from services.storage_service import MediaService, get_object

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["media"])


# Upload IDs are interpolated into a filesystem path (``/tmp/uploads/<id>``).
# Any non-alphanumeric character — including ``..``, ``/``, ``\`` — would
# allow path-traversal escapes (e.g. an attacker uploading to
# ``../../etc/cron.d/foo``). We require strictly safe characters and a
# tight length range. UUIDs (32 hex + 4 hyphens) and the legacy
# ``upload_<timestamp>_<rand>`` shape both pass.
_UPLOAD_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,128}$")


def _validate_upload_id(upload_id: str) -> str:
    """Reject anything that could escape ``/tmp/uploads/``.

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


@router.post("/media/upload")
async def upload_media(
    file: UploadFile = File(...),
    category: str = Form("general"),
):
    """Upload a media file (video, image, audio). Max 100MB."""
    try:
        data = await file.read()
        if len(data) < 100:
            raise HTTPException(status_code=400, detail="File is empty or too small")

        svc = MediaService()
        result = await svc.upload(
            filename=file.filename or "upload.bin",
            data=data,
            content_type=file.content_type or "application/octet-stream",
            uploaded_by="admin",
            category=category,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Upload error: {e}")
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")


@router.post("/media/upload-chunk")
async def upload_chunk(
    file: UploadFile = File(...),
    chunk_index: int = Form(0),
    total_chunks: int = Form(1),
    upload_id: str = Form(""),
    filename: str = Form("upload.bin"),
    category: str = Form("general"),
    content_type: str = Form("application/octet-stream"),
):
    """Upload a file in chunks for large files. Assembles on last chunk."""
    import shutil

    # Validate FIRST — never let a malicious upload_id touch the
    # filesystem. ``os.path.join`` doesn't protect against absolute
    # path injection, so the regex is the entire guard.
    upload_id = _validate_upload_id(upload_id)
    # ``/tmp/uploads`` is shared across processes on this host. The
    # B108 risk surface is symlink-attack + path-traversal via the
    # interpolated id. Both are now closed:
    #   * Regex above strictly limits the id charset (no ``..``, no ``/``)
    #   * The realpath check below rejects any resolved path that
    #     escapes the base, catching pre-created symlinks too.
    # This endpoint is also admin-gated upstream, so the threat
    # model further narrows to a misbehaving operator session.
    base_dir = "/tmp/uploads"  # nosec B108
    chunk_dir = os.path.join(base_dir, upload_id)
    # Defence-in-depth: even after the regex passes, verify the
    # resolved path stays inside ``/tmp/uploads``. Catches any
    # future regex regression without breaking the happy path.
    real_chunk_dir = os.path.realpath(chunk_dir)
    real_base = os.path.realpath(base_dir)
    if not real_chunk_dir.startswith(real_base + os.sep):
        raise HTTPException(status_code=400, detail="invalid upload path")
    os.makedirs(chunk_dir, exist_ok=True)

    chunk_data = await file.read()
    chunk_path = os.path.join(chunk_dir, f"chunk_{chunk_index:04d}")
    with open(chunk_path, "wb") as f:
        f.write(chunk_data)

    logger.info(f"Chunk {chunk_index + 1}/{total_chunks} received for {upload_id}")

    # If this is the last chunk, assemble and upload
    if chunk_index + 1 >= total_chunks:
        try:
            assembled = bytearray()
            for i in range(total_chunks):
                cp = os.path.join(chunk_dir, f"chunk_{i:04d}")
                with open(cp, "rb") as f:
                    assembled.extend(f.read())

            svc = MediaService()
            result = await svc.upload(
                filename=filename,
                data=bytes(assembled),
                content_type=content_type,
                uploaded_by="admin",
                category=category,
            )

            # Cleanup temp chunks
            shutil.rmtree(chunk_dir, ignore_errors=True)
            return {**result, "complete": True}
        except Exception as e:
            shutil.rmtree(chunk_dir, ignore_errors=True)
            logger.error(f"Chunk assembly error: {e}")
            raise HTTPException(status_code=500, detail=f"Assembly failed: {str(e)}")

    return {"chunk": chunk_index, "total": total_chunks, "complete": False}


@router.get("/media")
async def list_media(category: str = None):
    """List all uploaded media files."""
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
async def delete_media(file_id: str):
    """Soft-delete a media file."""
    svc = MediaService()
    deleted = await svc.delete_file(file_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="File not found")
    return {"deleted": True, "file_id": file_id}
