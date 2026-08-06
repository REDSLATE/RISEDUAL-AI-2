"""Tests for Resend removal (iter_187).

Verifies:
(a) `import services.email_service` succeeds even though the `resend`
    package is uninstalled from the venv.
(b) No active (non-test) source file references `resend.` or `_ResendEmails`.
(c) get_email_provider_pool returns only sendgrid entries when SENDGRID_API_KEY set.
(d) get_email_provider_pool returns [] when neither env var is set.
(e) `RESEND_API_KEY` is not a key in routes.vault's validators dict.
(f) Simulated send with monkey-patched httpx.AsyncClient asserts
    _send_via_sendgrid is called through _routed_send.
"""
import os
import sys
import importlib
import subprocess
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


# ---------- (a) module import without `resend` package present ----------

def test_resend_package_uninstalled():
    """The `resend` PyPI package must be absent from the environment."""
    result = subprocess.run(
        [sys.executable, "-c", "import resend"],
        capture_output=True, text=True,
    )
    assert result.returncode != 0, "resend package is still installed!"
    assert "ModuleNotFoundError" in result.stderr or "No module named" in result.stderr


def test_email_service_imports_cleanly():
    """services.email_service must import without ImportError."""
    # Force fresh import
    for mod in [m for m in list(sys.modules) if m.startswith("services.email_service")]:
        del sys.modules[mod]
    import services.email_service as es  # noqa: F401
    assert hasattr(es, "_send_via_sendgrid")
    assert hasattr(es, "_routed_send")
    assert hasattr(es, "_is_configured")
    assert not hasattr(es, "_send_via_resend")


# ---------- (b) grep assertions on active source ----------

def test_no_resend_references_in_active_code():
    """Scan backend/ for any remaining `resend.` or `_ResendEmails` references
    in non-test .py files. Comments/docstrings mentioning 'Resend removed'
    are allowed but `import resend`, `resend.Emails`, `_ResendEmails` are not.
    """
    backend = BACKEND_DIR
    banned_patterns = ["import resend", "from resend", "resend.Emails", "_ResendEmails"]
    offenders = []
    for py in backend.rglob("*.py"):
        parts = set(py.parts)
        if "tests" in parts or "__pycache__" in parts or "slow" in parts:
            continue
        try:
            text = py.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for pat in banned_patterns:
            if pat in text:
                offenders.append(f"{py}: {pat}")
    assert not offenders, f"Active code still references resend: {offenders}"


# ---------- (c)(d) pool_config.get_email_provider_pool ----------

def _reload_pool_config():
    import services.pool_config as pc
    return importlib.reload(pc)


def test_pool_returns_sendgrid_only_when_key_set(monkeypatch):
    monkeypatch.setenv("SENDGRID_API_KEY", "SG.test_key_xyz")
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.delenv("EMAIL_PROVIDER_POOL", raising=False)
    pc = _reload_pool_config()
    pool = pc.get_email_provider_pool()
    assert isinstance(pool, list) and len(pool) == 1
    entry = pool[0]
    assert entry["provider"] == "sendgrid"
    assert entry["name"] == "sendgrid-primary"
    assert entry["priority"] == 1
    assert entry["api_key"] == "SG.test_key_xyz"
    # Make sure no resend entry snuck in
    assert all("resend" not in str(e).lower() for e in pool)


def test_pool_empty_when_no_keys(monkeypatch):
    monkeypatch.delenv("SENDGRID_API_KEY", raising=False)
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.delenv("EMAIL_PROVIDER_POOL", raising=False)
    pc = _reload_pool_config()
    pool = pc.get_email_provider_pool()
    assert pool == []


# ---------- (e) routes.vault validators dict has no RESEND_API_KEY ----------

def test_vault_validators_has_no_resend():
    import routes.vault as vault_mod
    # Try to introspect the validators dict directly from source
    src = Path(vault_mod.__file__).read_text(encoding="utf-8")
    assert "_validate_resend" not in src, "vault.py still defines _validate_resend"
    assert "RESEND_API_KEY" not in src, "vault.py still references RESEND_API_KEY"
    assert "_validate_sendgrid" in src, "vault.py must still validate SendGrid"


# ---------- key_vault lane mapping ----------

def test_key_vault_email_lane_no_resend():
    src = (BACKEND_DIR / "services" / "key_vault.py").read_text(encoding="utf-8")
    assert "RESEND_API_KEY" not in src, "key_vault still maps RESEND_API_KEY"
    assert "SENDGRID_API_KEY" in src, "key_vault should still map SENDGRID_API_KEY"


# ---------- requirements.txt & .env checks ----------

def test_requirements_no_resend():
    reqs = (BACKEND_DIR / "requirements.txt").read_text(encoding="utf-8")
    for line in reqs.splitlines():
        stripped = line.strip().lower()
        if stripped.startswith("#") or not stripped:
            continue
        assert not stripped.startswith("resend"), f"requirements.txt still lists: {line}"


def test_env_resend_key_cleared():
    env_txt = (BACKEND_DIR / ".env").read_text(encoding="utf-8")
    for line in env_txt.splitlines():
        if line.startswith("RESEND_API_KEY="):
            # allowed to exist but must have empty value
            value = line.split("=", 1)[1].strip()
            assert value == "", f"RESEND_API_KEY still has a value: {value!r}"


# ---------- (f) simulated send through _routed_send hits sendgrid ----------

class _FakeResponse:
    def __init__(self, status_code=202, text_body=""):
        self.status_code = status_code
        self.text = text_body


class _FakeAsyncClient:
    last_url = None
    last_headers = None
    last_json = None

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, headers=None, json=None):
        _FakeAsyncClient.last_url = url
        _FakeAsyncClient.last_headers = headers
        _FakeAsyncClient.last_json = json
        return _FakeResponse(202, "")


@pytest.mark.asyncio
async def test_routed_send_dispatches_to_sendgrid(monkeypatch):
    """With a sendgrid-only pool, _routed_send should hit the SendGrid API."""
    monkeypatch.setenv("SENDGRID_API_KEY", "SG.fake_key_for_test")
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.delenv("EMAIL_PROVIDER_POOL", raising=False)

    # Reload pool_config, provider_registry, then email_service so the
    # module-level ProviderRouter picks up the new pool.
    import services.pool_config as pc
    importlib.reload(pc)
    import services.provider_registry as pr
    importlib.reload(pr)
    for mod in [m for m in list(sys.modules) if m.startswith("services.email_service")]:
        del sys.modules[mod]
    import services.email_service as es

    # Patch httpx.AsyncClient inside email_service
    monkeypatch.setattr(es.httpx, "AsyncClient", _FakeAsyncClient)

    assert es._is_configured() is True
    ok = await es._routed_send(["dest@example.com"], "TEST subject", "<p>hi</p>")
    assert ok is True
    assert _FakeAsyncClient.last_url == "https://api.sendgrid.com/v3/mail/send"
    assert _FakeAsyncClient.last_headers["Authorization"] == "Bearer SG.fake_key_for_test"
    assert _FakeAsyncClient.last_json["subject"] == "TEST subject"
    assert _FakeAsyncClient.last_json["personalizations"][0]["to"][0]["email"] == "dest@example.com"


@pytest.mark.asyncio
async def test_routed_send_returns_false_when_no_providers(monkeypatch):
    """With no provider keys, _routed_send returns False, does not raise."""
    monkeypatch.delenv("SENDGRID_API_KEY", raising=False)
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.delenv("EMAIL_PROVIDER_POOL", raising=False)

    import services.pool_config as pc
    importlib.reload(pc)
    import services.provider_registry as pr
    importlib.reload(pr)
    for mod in [m for m in list(sys.modules) if m.startswith("services.email_service")]:
        del sys.modules[mod]
    import services.email_service as es

    assert es._is_configured() is False
    ok = await es._routed_send(["a@b.com"], "s", "<p></p>")
    assert ok is False


# ---------- Public wrappers all funnel through _routed_send ----------

def test_public_wrappers_use_routed_send():
    """send_toxic_spikes_email, send_referral_success, send_welcome_referral_email,
    send_war_room_invite must all use _routed_send (no direct provider calls)."""
    src = (BACKEND_DIR / "services" / "email_service.py").read_text(encoding="utf-8")
    for fn in [
        "send_toxic_spikes_email",
        "send_referral_success",
        "send_welcome_referral_email",
        "send_war_room_invite",
    ]:
        # Locate function block and confirm it calls _routed_send within body
        idx = src.find(f"async def {fn}(")
        assert idx != -1, f"{fn} not found in email_service.py"
        # Grab next ~2000 chars as the function body window
        body = src[idx: idx + 2500]
        assert "_routed_send(" in body, f"{fn} does not use _routed_send"
