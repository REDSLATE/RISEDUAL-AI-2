"""Object storage service — wraps Emergent object storage API for media uploads."""
import os
import uuid
import logging
import requests
from datetime import datetime, timezone
from typing import Optional, Any
from motor.motor_asyncio import AsyncIOMotorClient

logger = logging.getLogger(__name__)

STORAGE_URL = "https://integrations.emergentagent.com/objstore/api/v1/storage"
APP_NAME = "risedual"

# Module-level storage key — initialized once
_storage_key: Optional[str] = None


def init_storage() -> str:
    """Initialize storage and get session-scoped key. Call once at startup."""
    global _storage_key
    if _storage_key:
        return _storage_key
    key = os.environ.get("EMERGENT_LLM_KEY")
    if not key:
        raise RuntimeError("EMERGENT_LLM_KEY not set")
    resp = requests.post(
        f"{STORAGE_URL}/init",
        json={"emergent_key": key},
        timeout=30,
    )
    resp.raise_for_status()
    _storage_key = resp.json()["storage_key"]
    logger.info("Object storage initialized successfully")
    return _storage_key


def put_object(path: str, data: bytes, content_type: str) -> dict:
    """Upload bytes to storage. Returns {"path": ..., "size": ..., "etag": ...}."""
    key = init_storage()
    resp = requests.put(
        f"{STORAGE_URL}/objects/{path}",
        headers={"X-Storage-Key": key, "Content-Type": content_type},
        data=data,
        timeout=180,
    )
    resp.raise_for_status()
    return resp.json()


def get_object(path: str) -> tuple[bytes, str]:
    """Download file from storage. Returns (content_bytes, content_type)."""
    key = init_storage()
    resp = requests.get(
        f"{STORAGE_URL}/objects/{path}",
        headers={"X-Storage-Key": key},
        timeout=120,
    )
    resp.raise_for_status()
    return resp.content, resp.headers.get("Content-Type", "application/octet-stream")


MIME_MAP = {
    "mp4": "video/mp4", "mov": "video/quicktime", "avi": "video/x-msvideo",
    "webm": "video/webm", "mkv": "video/x-matroska",
    "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
    "gif": "image/gif", "webp": "image/webp", "svg": "image/svg+xml",
    "mp3": "audio/mpeg", "wav": "audio/wav", "ogg": "audio/ogg",
    "pdf": "application/pdf", "txt": "text/plain",
}

ALLOWED_TYPES = {"video", "image", "audio", "application"}
MAX_SIZE = 100 * 1024 * 1024  # 100MB


class MediaService:
    def __init__(self) -> None:
        self.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        self.db = self.client[os.environ.get("DB_NAME", "risedual_db")]
        self.col = self.db["media_files"]

    async def upload(self, filename: str, data: bytes, content_type: str, uploaded_by: str = "admin", category: str = "general") -> dict:
        """Upload a file and store reference in MongoDB."""
        if len(data) > MAX_SIZE:
            raise ValueError(f"File too large ({len(data)} bytes). Max {MAX_SIZE // 1024 // 1024}MB.")

        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "bin"
        if not content_type or content_type == "application/octet-stream":
            content_type = MIME_MAP.get(ext, "application/octet-stream")

        file_id = str(uuid.uuid4())
        storage_path = f"{APP_NAME}/media/{category}/{file_id}.{ext}"

        result = put_object(storage_path, data, content_type)

        doc = {
            "file_id": file_id,
            "storage_path": result["path"],
            "original_filename": filename,
            "content_type": content_type,
            "size": result.get("size", len(data)),
            "category": category,
            "uploaded_by": uploaded_by,
            "is_deleted": False,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        await self.col.insert_one(doc)

        return {
            "file_id": file_id,
            "storage_path": result["path"],
            "filename": filename,
            "content_type": content_type,
            "size": len(data),
            "category": category,
        }

    async def list_media(self, category: str = None) -> list:
        """List all active (non-deleted) media files."""
        query: dict[str, Any] = {"is_deleted": False}
        if category:
            query["category"] = category
        cursor = self.col.find(query, {"_id": 0}).sort("created_at", -1)
        return await cursor.to_list(length=100)

    async def get_file_record(self, file_id: str) -> dict:
        """Get a single file record by file_id."""
        return await self.col.find_one(
            {"file_id": file_id, "is_deleted": False}, {"_id": 0}
        )

    async def delete_file(self, file_id: str) -> bool:
        """Soft-delete a file."""
        result = await self.col.update_one(
            {"file_id": file_id}, {"$set": {"is_deleted": True}}
        )
        return result.modified_count > 0

    async def get_landing_video(self) -> dict:
        """Get the latest video uploaded to the 'landing' category."""
        return await self.col.find_one(
            {"category": "landing", "is_deleted": False, "content_type": {"$regex": "^video/"}},
            {"_id": 0},
            sort=[("created_at", -1)],
        )
