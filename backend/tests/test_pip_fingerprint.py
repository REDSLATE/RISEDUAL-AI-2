"""Tests for the pip-freeze environment fingerprint on Alpha's
RuntimeStamp (Stage 3.5 — environment drift detection).

These pin three invariants:

1. ``env_pip_fingerprint`` returns a stable shape (hash + count +
   sample + error + captured_at_ms) even when ``pip freeze`` fails.
2. The fingerprint is canonicalised — re-ordering pip's output
   between captures must NOT change the hash.
3. ``RuntimeStamp.current()`` now embeds the fingerprint, so MC
   automatically sees the package manifest on every check-in.
"""
from __future__ import annotations

import hashlib
import subprocess
from dataclasses import asdict

import pytest

from shared.runtime import platform_survival as ps
from shared.runtime.platform_survival import (
    RuntimeStamp,
    env_pip_fingerprint,
)


@pytest.fixture(autouse=True)
def _clear_pip_fp_cache():
    """Each test starts with a fresh fingerprint cache."""
    ps._PIP_FINGERPRINT_CACHE = None
    yield
    ps._PIP_FINGERPRINT_CACHE = None


def test_env_pip_fingerprint_shape():
    fp = env_pip_fingerprint()
    # Mandatory keys present.
    assert set(fp.keys()) >= {
        "pip_freeze_sha256", "package_count", "sample",
        "error", "captured_at_ms",
    }
    # Hash is hex sha256 (64 chars) or None when failed.
    h = fp["pip_freeze_sha256"]
    assert h is None or (isinstance(h, str) and len(h) == 64)
    assert isinstance(fp["package_count"], int)
    assert isinstance(fp["sample"], list)
    assert isinstance(fp["captured_at_ms"], int)
    assert fp["captured_at_ms"] > 0


def test_env_pip_fingerprint_real_capture_succeeds_in_this_env():
    """Pip IS installed in our test env, so the real path should work."""
    fp = env_pip_fingerprint()
    assert fp["error"] is None, fp["error"]
    assert fp["package_count"] > 10  # we install >>10 packages
    assert fp["pip_freeze_sha256"] is not None


def test_env_pip_fingerprint_cache_returns_same_dict():
    fp1 = env_pip_fingerprint()
    fp2 = env_pip_fingerprint()
    # Same object reference == cache hit
    assert fp1 is fp2


def test_env_pip_fingerprint_force_refresh_recaptures():
    fp1 = env_pip_fingerprint()
    fp2 = env_pip_fingerprint(force_refresh=True)
    assert fp1 is not fp2
    # Same env → same hash even on re-capture.
    assert fp1["pip_freeze_sha256"] == fp2["pip_freeze_sha256"]


def test_env_pip_fingerprint_hash_is_order_independent(monkeypatch):
    """Two captures producing the same lines in different order
    must hash identically (canonical sort)."""

    class _FakeProc:
        def __init__(self, stdout):
            self.stdout = stdout
            self.returncode = 0

    captures = [
        "scikit-learn==1.4.2\nnumpy==1.26.4\nfastapi==0.110.1\n",
        "fastapi==0.110.1\nnumpy==1.26.4\nscikit-learn==1.4.2\n",
    ]
    calls = {"n": 0}

    def fake_run(*a, **kw):
        out = captures[calls["n"]]
        calls["n"] += 1
        return _FakeProc(out)

    monkeypatch.setattr(subprocess, "run", fake_run)

    ps._PIP_FINGERPRINT_CACHE = None
    fp1 = env_pip_fingerprint()
    ps._PIP_FINGERPRINT_CACHE = None
    fp2 = env_pip_fingerprint()

    assert fp1["pip_freeze_sha256"] == fp2["pip_freeze_sha256"]
    assert fp1["package_count"] == 3


def test_env_pip_fingerprint_detects_drift(monkeypatch):
    """A different package list MUST produce a different hash."""

    class _FakeProc:
        def __init__(self, stdout):
            self.stdout = stdout
            self.returncode = 0

    captures = [
        "scikit-learn==1.4.2\nnumpy==1.26.4\n",
        "scikit-learn==1.5.0\nnumpy==1.26.4\n",
    ]
    calls = {"n": 0}

    def fake_run(*a, **kw):
        out = captures[calls["n"]]
        calls["n"] += 1
        return _FakeProc(out)

    monkeypatch.setattr(subprocess, "run", fake_run)

    ps._PIP_FINGERPRINT_CACHE = None
    fp1 = env_pip_fingerprint()
    ps._PIP_FINGERPRINT_CACHE = None
    fp2 = env_pip_fingerprint()

    assert fp1["pip_freeze_sha256"] != fp2["pip_freeze_sha256"]


def test_env_pip_fingerprint_handles_pip_not_found(monkeypatch):
    def raises_fnf(*a, **kw):
        raise FileNotFoundError("pip")
    monkeypatch.setattr(subprocess, "run", raises_fnf)
    ps._PIP_FINGERPRINT_CACHE = None
    fp = env_pip_fingerprint()
    assert fp["error"] == "pip_not_found"
    assert fp["pip_freeze_sha256"] is None
    assert fp["package_count"] == 0


def test_env_pip_fingerprint_handles_timeout(monkeypatch):
    def raises_timeout(*a, **kw):
        raise subprocess.TimeoutExpired("pip freeze", 10)
    monkeypatch.setattr(subprocess, "run", raises_timeout)
    ps._PIP_FINGERPRINT_CACHE = None
    fp = env_pip_fingerprint()
    assert fp["error"] == "pip_freeze_timeout"


def test_env_pip_fingerprint_handles_nonzero_returncode(monkeypatch):
    class _FakeProc:
        stdout = ""
        returncode = 2
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _FakeProc())
    ps._PIP_FINGERPRINT_CACHE = None
    fp = env_pip_fingerprint()
    assert fp["error"] == "pip_returncode_2"
    # Hash of empty canonical string is still computed (it's a valid sha)
    assert fp["pip_freeze_sha256"] == hashlib.sha256(b"").hexdigest()


def test_runtime_stamp_includes_pip_fingerprint():
    stamp = RuntimeStamp.current(sidecar_room="alpha-room")
    d = asdict(stamp)
    assert "pip_fingerprint" in d
    assert "pip_freeze_sha256" in d["pip_fingerprint"]
    assert d["pip_fingerprint"]["package_count"] > 0


def test_runtime_stamp_is_json_serializable():
    """MC posts the stamp as JSON — every field must survive
    json.dumps round-trip."""
    import json
    stamp = RuntimeStamp.current(sidecar_room="alpha-room")
    payload = json.dumps(asdict(stamp))
    back = json.loads(payload)
    assert back["pip_fingerprint"]["pip_freeze_sha256"] is not None
