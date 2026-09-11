"""Live integration tests for SEC-001 (media auth) and broker watchdog admin endpoints."""
import io
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://risedual-trading.preview.emergentagent.com").rstrip("/")
ADMIN_EMAIL = "admin@risedual.ai"
ADMIN_PASSWORD = "RiseDual2026!"


@pytest.fixture(scope="module")
def anon():
    return requests.Session()


@pytest.fixture(scope="module")
def admin_session():
    s = requests.Session()
    r = s.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        headers={"X-Requested-With": "XMLHttpRequest"},
        timeout=20,
    )
    if r.status_code != 200:
        pytest.skip(f"admin login failed status={r.status_code} body={r.text[:200]}")
    return s


# --- SEC-001 unauth ---

def test_upload_unauth_blocked(anon):
    files = {"file": ("t.png", io.BytesIO(b"x" * 32), "image/png")}
    r = anon.post(f"{BASE_URL}/api/media/upload", files=files, timeout=15)
    assert r.status_code in (401, 403), r.text


def test_delete_unauth_blocked(anon):
    r = anon.delete(f"{BASE_URL}/api/media/nonexistent-id", timeout=15)
    assert r.status_code in (401, 403), r.text


def test_list_unauth_blocked(anon):
    r = anon.get(f"{BASE_URL}/api/media", timeout=15)
    assert r.status_code in (401, 403), r.text


def test_upload_chunk_unauth_blocked(anon):
    r = anon.post(
        f"{BASE_URL}/api/media/upload-chunk",
        data={"upload_id": "abc123", "chunk_index": "0", "total_chunks": "1", "filename": "a.png"},
        files={"file": ("c", io.BytesIO(b"x" * 128), "application/octet-stream")},
        timeout=15,
    )
    assert r.status_code in (401, 403), r.text


def test_landing_video_is_public(anon):
    r = anon.get(f"{BASE_URL}/api/media/landing-video", timeout=15, allow_redirects=False)
    # 200 OK expected, allow 204/302 if no landing set but must NOT be 401/403
    assert r.status_code not in (401, 403), r.text
    assert r.status_code < 500


# --- SEC-001 authed upload ---

def test_authed_upload_uses_token_email(admin_session):
    files = {"file": ("TEST_sec001.png", io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"0" * 512), "image/png")}
    r = admin_session.post(
        f"{BASE_URL}/api/media/upload",
        files=files,
        headers={"X-Requested-With": "XMLHttpRequest"},
        timeout=30,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    file_id = body.get("file_id") or body.get("id") or (body.get("record") or {}).get("id")
    assert file_id, f"expected file_id in upload response, got {body}"

    # Verify uploaded_by via list endpoint
    lst = admin_session.get(f"{BASE_URL}/api/media?category=general", timeout=15)
    assert lst.status_code == 200, lst.text
    items = lst.json()
    if isinstance(items, dict):
        items = items.get("files") or items.get("items") or items.get("media") or items.get("records") or []
    match = next((m for m in items if (m.get("file_id") == file_id or m.get("id") == file_id)), None)
    assert match, f"uploaded record not found in list; file_id={file_id}"
    uploaded_by = match.get("uploaded_by")
    assert uploaded_by and uploaded_by != "admin", f"uploaded_by should derive from token, got {uploaded_by!r}"
    assert "@" in str(uploaded_by) or ADMIN_EMAIL.split("@")[0] in str(uploaded_by).lower(), f"expected email-like uploaded_by, got {uploaded_by!r}"

    # cleanup
    admin_session.delete(f"{BASE_URL}/api/media/{file_id}", headers={"X-Requested-With": "XMLHttpRequest"}, timeout=15)


# --- Watchdog admin endpoints ---

def test_watchdog_list_unauth_blocked(anon):
    r = anon.get(f"{BASE_URL}/api/admin/alpha-daytrader/broker-watchdog", timeout=15)
    assert r.status_code in (401, 403), r.text


def test_watchdog_list_admin_ok(admin_session):
    r = admin_session.get(f"{BASE_URL}/api/admin/alpha-daytrader/broker-watchdog", timeout=15)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "entries" in body and "count" in body, body
    assert isinstance(body["entries"], list)
    assert isinstance(body["count"], int)


def test_watchdog_clear_nonexistent_returns_404(admin_session):
    r = admin_session.post(
        f"{BASE_URL}/api/admin/alpha-daytrader/broker-watchdog/clear/public/nonexistent-coid",
        headers={"X-Requested-With": "XMLHttpRequest"},
        timeout=15,
    )
    assert r.status_code == 404, f"expected 404 got {r.status_code} body={r.text}"


def test_watchdog_clear_unauth_blocked(anon):
    r = anon.post(
        f"{BASE_URL}/api/admin/alpha-daytrader/broker-watchdog/clear/public/nonexistent-coid",
        timeout=15,
    )
    assert r.status_code in (401, 403), r.text
