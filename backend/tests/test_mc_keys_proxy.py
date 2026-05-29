"""Tests — services.mc_keys_proxy.

Covers the boot-time MC keys proxy:
- env-driven enable/disable
- missing config short-circuits (no crash)
- successful fetch + stamp into os.environ
- failure modes leave local env unchanged
- empty-string MC values do NOT zero out local working keys
"""
from __future__ import annotations

import os
from unittest.mock import patch

import httpx

from services.mc_keys_proxy import (
    apply_keys_to_environ,
    fetch_and_apply,
    fetch_market_data_keys,
)


# ── fetch_market_data_keys ────────────────────────────────────────


def test_disabled_via_env(monkeypatch):
    monkeypatch.setenv("MC_KEYS_PROXY_ENABLED", "false")
    assert fetch_market_data_keys() is None


def test_missing_base_url_skips(monkeypatch):
    monkeypatch.delenv("RISEDUAL_MC_URL", raising=False)
    monkeypatch.delenv("MC_BASE_URL", raising=False)
    monkeypatch.setenv("ALPHA_INGEST_TOKEN", "tok")
    monkeypatch.setenv("MC_KEYS_PROXY_ENABLED", "true")
    assert fetch_market_data_keys() is None


def test_missing_token_skips(monkeypatch):
    monkeypatch.setenv("RISEDUAL_MC_URL", "https://mission.risedual.ai")
    monkeypatch.delenv("ALPHA_INGEST_TOKEN", raising=False)
    monkeypatch.delenv("ALPHA_MC_INGEST_TOKEN", raising=False)
    monkeypatch.setenv("MC_KEYS_PROXY_ENABLED", "true")
    assert fetch_market_data_keys() is None


def test_successful_fetch_returns_keys(monkeypatch):
    monkeypatch.setenv("RISEDUAL_MC_URL", "https://mission.risedual.ai")
    monkeypatch.setenv("ALPHA_MC_INGEST_TOKEN", "tok")
    monkeypatch.setenv("ALPHA_INGEST_TOKEN", "tok")
    monkeypatch.setenv("MC_KEYS_PROXY_ENABLED", "true")

    class _FakeResp:
        status_code = 200
        text = ""

        def json(self):
            return {"keys": {"POLYGON_API_KEY": "pkey", "FINNHUB_API_KEY": "fkey"}}

    class _FakeClient:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def get(self, url, headers):
            assert headers["X-Runtime-Token"] == "tok"
            assert headers["X-Brain-Id"]  # default "alpha"
            return _FakeResp()

    monkeypatch.setattr("services.mc_keys_proxy.httpx.Client", _FakeClient)
    result = fetch_market_data_keys()
    assert result == {"POLYGON_API_KEY": "pkey", "FINNHUB_API_KEY": "fkey"}


def test_http_4xx_returns_none(monkeypatch):
    monkeypatch.setenv("RISEDUAL_MC_URL", "https://mission.risedual.ai")
    monkeypatch.setenv("ALPHA_MC_INGEST_TOKEN", "tok")
    monkeypatch.setenv("ALPHA_INGEST_TOKEN", "tok")
    monkeypatch.setenv("MC_KEYS_PROXY_ENABLED", "true")

    class _FakeResp:
        status_code = 403
        text = "Forbidden"

    class _FakeClient:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def get(self, *a, **kw):
            return _FakeResp()

    monkeypatch.setattr("services.mc_keys_proxy.httpx.Client", _FakeClient)
    assert fetch_market_data_keys() is None


def test_transport_error_returns_none(monkeypatch):
    monkeypatch.setenv("RISEDUAL_MC_URL", "https://mission.risedual.ai")
    monkeypatch.setenv("ALPHA_MC_INGEST_TOKEN", "tok")
    monkeypatch.setenv("ALPHA_INGEST_TOKEN", "tok")
    monkeypatch.setenv("MC_KEYS_PROXY_ENABLED", "true")

    class _FakeClient:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def get(self, *a, **kw):
            raise httpx.ConnectError("no route")

    monkeypatch.setattr("services.mc_keys_proxy.httpx.Client", _FakeClient)
    assert fetch_market_data_keys() is None


def test_schema_missing_keys_field(monkeypatch):
    monkeypatch.setenv("RISEDUAL_MC_URL", "https://mission.risedual.ai")
    monkeypatch.setenv("ALPHA_MC_INGEST_TOKEN", "tok")
    monkeypatch.setenv("ALPHA_INGEST_TOKEN", "tok")
    monkeypatch.setenv("MC_KEYS_PROXY_ENABLED", "true")

    class _FakeResp:
        status_code = 200
        text = ""

        def json(self):
            return {"something_else": True}

    class _FakeClient:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def get(self, *a, **kw):
            return _FakeResp()

    monkeypatch.setattr("services.mc_keys_proxy.httpx.Client", _FakeClient)
    assert fetch_market_data_keys() is None


def test_non_string_values_dropped(monkeypatch):
    monkeypatch.setenv("RISEDUAL_MC_URL", "https://mission.risedual.ai")
    monkeypatch.setenv("ALPHA_MC_INGEST_TOKEN", "tok")
    monkeypatch.setenv("ALPHA_INGEST_TOKEN", "tok")
    monkeypatch.setenv("MC_KEYS_PROXY_ENABLED", "true")

    class _FakeResp:
        status_code = 200
        text = ""

        def json(self):
            return {"keys": {"POLYGON_API_KEY": "ok", "FINNHUB_API_KEY": 42, "X": None}}

    class _FakeClient:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def get(self, *a, **kw):
            return _FakeResp()

    monkeypatch.setattr("services.mc_keys_proxy.httpx.Client", _FakeClient)
    result = fetch_market_data_keys()
    assert result == {"POLYGON_API_KEY": "ok"}


# ── apply_keys_to_environ ─────────────────────────────────────────


def test_apply_stamps_into_environ(monkeypatch):
    monkeypatch.delenv("POLYGON_API_KEY", raising=False)
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    applied = apply_keys_to_environ(
        {"POLYGON_API_KEY": "p123", "FINNHUB_API_KEY": "f456"},
    )
    assert sorted(applied) == ["FINNHUB_API_KEY", "POLYGON_API_KEY"]
    assert os.environ["POLYGON_API_KEY"] == "p123"
    assert os.environ["FINNHUB_API_KEY"] == "f456"


def test_apply_skips_empty_values_preserves_local(monkeypatch):
    """An MC blank must NOT zero out a working local key."""
    monkeypatch.setenv("POLYGON_API_KEY", "local-polygon")
    monkeypatch.setenv("FINNHUB_API_KEY", "local-finnhub")
    applied = apply_keys_to_environ(
        {"POLYGON_API_KEY": "", "FINNHUB_API_KEY": "   "},
    )
    assert applied == []
    assert os.environ["POLYGON_API_KEY"] == "local-polygon"
    assert os.environ["FINNHUB_API_KEY"] == "local-finnhub"


def test_apply_ignores_unknown_keys(monkeypatch):
    monkeypatch.delenv("RANDOM_OTHER_KEY", raising=False)
    applied = apply_keys_to_environ({"RANDOM_OTHER_KEY": "xyz"})
    assert applied == []
    assert "RANDOM_OTHER_KEY" not in os.environ


# ── fetch_and_apply (boot path) ───────────────────────────────────


def test_fetch_and_apply_skipped_when_fetch_fails(monkeypatch):
    monkeypatch.setenv("MC_KEYS_PROXY_ENABLED", "false")
    result = fetch_and_apply()
    assert result["status"] == "skipped"
    assert result["applied"] == []


def test_fetch_and_apply_applied_when_keys_returned(monkeypatch):
    monkeypatch.delenv("POLYGON_API_KEY", raising=False)
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    with patch(
        "services.mc_keys_proxy.fetch_market_data_keys",
        return_value={"POLYGON_API_KEY": "Pk", "FINNHUB_API_KEY": "Fk"},
    ):
        result = fetch_and_apply()
    assert result["status"] == "applied"
    assert sorted(result["applied"]) == ["FINNHUB_API_KEY", "POLYGON_API_KEY"]
    assert os.environ["POLYGON_API_KEY"] == "Pk"
    assert os.environ["FINNHUB_API_KEY"] == "Fk"


def test_fetch_and_apply_no_usable_keys(monkeypatch):
    monkeypatch.setenv("POLYGON_API_KEY", "local")
    with patch(
        "services.mc_keys_proxy.fetch_market_data_keys",
        return_value={"POLYGON_API_KEY": ""},
    ):
        result = fetch_and_apply()
    assert result["status"] == "skipped"
    assert result["applied"] == []
    # Local value preserved.
    assert os.environ["POLYGON_API_KEY"] == "local"
