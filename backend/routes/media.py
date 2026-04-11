"""Media upload/download routes — admin media manager for videos, images, etc."""
import os
import logging
from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Response, Request
from services.storage_service import MediaService, get_object, init_storage

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["media"])


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
    import tempfile
    import shutil

    chunk_dir = f"/tmp/uploads/{upload_id}"
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
