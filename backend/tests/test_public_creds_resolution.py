"""Verify V2/Legacy Public cred resolution reads every source the operator
may have used: env, plaintext connected row, and the ENCRYPTED broker-connect
panel record (prod's actual shape)."""
import pytest


class _FakeColl:
    def __init__(self, connected=None, panel=None):
        self._connected = connected
        self._panel = panel

    async def find_one(self, flt, projection=None, sort=None):
        if flt.get("status") == "connected":
            return self._connected
        if flt.get("is_active") is True:
            return self._panel
        return None


class _FakeDB:
    def __init__(self, coll):
        self.broker_connections = coll


@pytest.mark.asyncio
async def test_resolve_env_first(monkeypatch):
    monkeypatch.setenv("PUBLIC_API_KEY", "ENVSECRET")
    monkeypatch.setenv("PUBLIC_ACCOUNT_ID", "ENVACCT")
    from services.public_equity_live_executor import _aresolve_connect_creds
    assert await _aresolve_connect_creds(None) == ("ENVSECRET", "ENVACCT")


@pytest.mark.asyncio
async def test_resolve_plaintext_connected_row(monkeypatch):
    monkeypatch.delenv("PUBLIC_API_KEY", raising=False)
    monkeypatch.delenv("PUBLIC_ACCOUNT_ID", raising=False)
    from services.public_equity_live_executor import _aresolve_connect_creds
    db = _FakeDB(_FakeColl(connected={"api_key": "PK", "api_secret": "PA"}))
    assert await _aresolve_connect_creds(db) == ("PK", "PA")


@pytest.mark.asyncio
async def test_resolve_from_encrypted_panel_record(monkeypatch):
    # prod's actual shape: encrypted api_key_enc/api_secret_enc, is_active.
    monkeypatch.delenv("PUBLIC_API_KEY", raising=False)
    monkeypatch.delenv("PUBLIC_ACCOUNT_ID", raising=False)
    from routes.broker import encrypt_value
    from services.public_equity_live_executor import _aresolve_connect_creds
    panel = {
        "broker_id": "public", "is_active": True,
        "api_key_enc": encrypt_value("SECRET123"),
        "api_secret_enc": encrypt_value("5LG34065"),
        "account_id": "5LG34065",
    }
    db = _FakeDB(_FakeColl(connected=None, panel=panel))
    assert await _aresolve_connect_creds(db) == ("SECRET123", "5LG34065")


@pytest.mark.asyncio
async def test_resolve_none_when_nothing_connected(monkeypatch):
    monkeypatch.delenv("PUBLIC_API_KEY", raising=False)
    monkeypatch.delenv("PUBLIC_ACCOUNT_ID", raising=False)
    from services.public_equity_live_executor import _aresolve_connect_creds
    db = _FakeDB(_FakeColl(connected=None, panel=None))
    assert await _aresolve_connect_creds(db) is None
