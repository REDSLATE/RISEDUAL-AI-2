"""SEC-001 regression — media upload/list/delete require admin auth,
``uploaded_by`` is derived from the token, and chunk staging is bounded.

Public read paths (``/media/landing-video``, ``/media/file/{id}``) stay
open — that's the deliberate design.
"""
from __future__ import annotations

import io
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


# ---------- helpers ---------------------------------------------------------

def _build_app(user_ret):
    """Fresh FastAPI + media router with a stubbed auth helper.

    ``user_ret`` is either a dict (authorised user) or ``None`` (unauth).
    """
    import routes.media as media_mod

    async def _fake_get_current_user(request):
        return user_ret

    # Patch at the module attribute the router actually calls.
    media_mod.get_current_user = _fake_get_current_user  # type: ignore

    app = FastAPI()
    app.include_router(media_mod.router)
    return app, media_mod


class _FakeMediaService:
    """Captures upload() invocations for assertion. Also serves list/delete."""

    last_kwargs: dict = {}

    async def upload(self, *, filename, data, content_type, uploaded_by, category):
        _FakeMediaService.last_kwargs = dict(
            filename=filename,
            data_len=len(data),
            content_type=content_type,
            uploaded_by=uploaded_by,
            category=category,
        )
        return {"file_id": "abc123", "size": len(data)}

    async def list_media(self, category=None):
        return [{"file_id": "abc123", "category": category or "general"}]

    async def delete_file(self, file_id):
        return True


@pytest.fixture(autouse=True)
def _patch_media_service(monkeypatch):
    import routes.media as media_mod
    monkeypatch.setattr(media_mod, "MediaService", _FakeMediaService)
    _FakeMediaService.last_kwargs = {}
    yield


# ---------- unauth callers ---------------------------------------------------

def test_upload_rejects_unauthenticated():
    app, _ = _build_app(None)
    client = TestClient(app)
    r = client.post(
        "/api/media/upload",
        files={"file": ("x.png", b"a" * 200, "image/png")},
    )
    assert r.status_code == 403
    assert _FakeMediaService.last_kwargs == {}


def test_upload_rejects_non_admin_role():
    app, _ = _build_app({"role": "user", "email": "u@x.com"})
    client = TestClient(app)
    r = client.post(
        "/api/media/upload",
        files={"file": ("x.png", b"a" * 200, "image/png")},
    )
    assert r.status_code == 403


def test_list_media_rejects_unauthenticated():
    app, _ = _build_app(None)
    r = TestClient(app).get("/api/media")
    assert r.status_code == 403


def test_delete_rejects_unauthenticated():
    app, _ = _build_app(None)
    r = TestClient(app).delete("/api/media/xyz")
    assert r.status_code == 403


def test_upload_chunk_rejects_unauthenticated():
    app, _ = _build_app(None)
    r = TestClient(app).post(
        "/api/media/upload-chunk",
        files={"file": ("x.png", b"a" * 100, "image/png")},
        data={"chunk_index": 0, "total_chunks": 1, "upload_id": "abcdefgh"},
    )
    assert r.status_code == 403


# ---------- admin happy path -------------------------------------------------

def test_upload_records_uploader_from_token():
    app, _ = _build_app({"role": "admin", "email": "admin@risedual.ai"})
    client = TestClient(app)
    r = client.post(
        "/api/media/upload",
        files={"file": ("x.png", b"a" * 500, "image/png")},
    )
    assert r.status_code == 200
    assert _FakeMediaService.last_kwargs["uploaded_by"] == "admin@risedual.ai"


def test_upload_records_uploader_prefers_email_over_id():
    app, _ = _build_app({
        "role": "owner", "email": "boss@risedual.ai", "id": "user_42",
    })
    r = TestClient(app).post(
        "/api/media/upload",
        files={"file": ("x.png", b"a" * 500, "image/png")},
    )
    assert r.status_code == 200
    assert _FakeMediaService.last_kwargs["uploaded_by"] == "boss@risedual.ai"


def test_upload_falls_back_to_id_when_no_email():
    app, _ = _build_app({"role": "admin", "id": "user_42"})
    r = TestClient(app).post(
        "/api/media/upload",
        files={"file": ("x.png", b"a" * 500, "image/png")},
    )
    assert r.status_code == 200
    assert _FakeMediaService.last_kwargs["uploaded_by"] == "user_42"


def test_chunked_upload_uses_authenticated_uploader():
    app, _ = _build_app({"role": "admin", "email": "admin@risedual.ai"})
    client = TestClient(app)
    payload = b"a" * 2000
    r = client.post(
        "/api/media/upload-chunk",
        files={"file": ("part0", payload, "application/octet-stream")},
        data={
            "chunk_index": 0,
            "total_chunks": 1,
            "upload_id": "unittest-chunk-001",
            "filename": "movie.bin",
            "content_type": "application/octet-stream",
        },
    )
    assert r.status_code == 200
    assert r.json().get("complete") is True
    assert _FakeMediaService.last_kwargs["uploaded_by"] == "admin@risedual.ai"


# ---------- chunk caps -------------------------------------------------------

def test_upload_chunk_rejects_oversized_single_chunk(monkeypatch):
    """Per-chunk cap fires before staging so memory can't be pinned."""
    import routes.media as media_mod
    monkeypatch.setattr(media_mod, "_MAX_CHUNK_BYTES", 1024)  # 1 KiB
    app, _ = _build_app({"role": "admin", "email": "admin@risedual.ai"})
    r = TestClient(app).post(
        "/api/media/upload-chunk",
        files={"file": ("p", b"a" * 4096, "application/octet-stream")},
        data={"chunk_index": 0, "total_chunks": 4, "upload_id": "chunkcap-001"},
    )
    assert r.status_code == 413


def test_upload_chunk_rejects_cumulative_overflow(monkeypatch):
    """Total-upload cap fires across multiple chunks."""
    import routes.media as media_mod
    # Chunk cap large enough that individual chunks pass; upload cap low.
    monkeypatch.setattr(media_mod, "_MAX_CHUNK_BYTES", 4096)
    monkeypatch.setattr(media_mod, "_MAX_UPLOAD_BYTES", 6000)
    app, _ = _build_app({"role": "admin", "email": "admin@risedual.ai"})
    client = TestClient(app)
    ok = client.post(
        "/api/media/upload-chunk",
        files={"file": ("p0", b"a" * 4000, "application/octet-stream")},
        data={"chunk_index": 0, "total_chunks": 2, "upload_id": "cumcap-001"},
    )
    assert ok.status_code == 200
    bad = client.post(
        "/api/media/upload-chunk",
        files={"file": ("p1", b"b" * 4000, "application/octet-stream")},
        data={"chunk_index": 1, "total_chunks": 2, "upload_id": "cumcap-001"},
    )
    assert bad.status_code == 413


def test_upload_rejects_oversized_single_file(monkeypatch):
    import routes.media as media_mod
    monkeypatch.setattr(media_mod, "_MAX_UPLOAD_BYTES", 1024)
    app, _ = _build_app({"role": "admin", "email": "admin@risedual.ai"})
    r = TestClient(app).post(
        "/api/media/upload",
        files={"file": ("x.png", b"a" * 4096, "image/png")},
    )
    assert r.status_code == 413


# ---------- public read paths remain open -----------------------------------

def test_landing_video_is_public(monkeypatch):
    """No auth required for the pre-login landing page."""
    import routes.media as media_mod

    class _Svc:
        async def get_landing_video(self):
            return None

    monkeypatch.setattr(media_mod, "MediaService", _Svc)
    app, _ = _build_app(None)
    r = TestClient(app).get("/api/media/landing-video")
    assert r.status_code == 200
    assert r.json() == {"has_video": False}


def test_upload_id_validation_still_fires():
    app, _ = _build_app({"role": "admin", "email": "admin@risedual.ai"})
    r = TestClient(app).post(
        "/api/media/upload-chunk",
        files={"file": ("p", b"a" * 100, "application/octet-stream")},
        data={"chunk_index": 0, "total_chunks": 1, "upload_id": "!!badid"},
    )
    assert r.status_code == 400
