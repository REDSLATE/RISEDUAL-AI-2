"""Tests for services.ops_snapshot — the admin health/config readout.

Covers:
- _collect_flags surfaces known + auto-detected prefixed flags.
- _collect_integrations reports configured booleans without leaking values.
- _probe_mongo handles missing handle / handle-without-client / ping success.
- _collect_scheduler_heartbeat reports the freshest of multiple collections.
- _collect_tier3_progress passes through readiness service output.
- _heuristic_notes fires the right messages for every condition.
- collect_ops_snapshot returns a stable schema even when probes fail.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from services import ops_snapshot as ops


# ── _collect_flags ────────────────────────────────────────────────


def test_collect_flags_known_unset_returned_as_none(monkeypatch):
    for f in ops.KNOWN_FLAGS:
        monkeypatch.delenv(f, raising=False)
    known, _extras = ops._collect_flags()
    # Every KNOWN_FLAGS entry must be present, value None.
    assert set(known.keys()) == set(ops.KNOWN_FLAGS)
    assert all(v is None for v in known.values())


def test_collect_flags_known_set_returns_value(monkeypatch):
    monkeypatch.setenv("CRYPTO_ADVERSARIAL_PHASE", "shadow")
    monkeypatch.setenv("PUBLIC_DATA_FLOOR_DATE", "2026-04-23")
    known, _extras = ops._collect_flags()
    assert known["CRYPTO_ADVERSARIAL_PHASE"] == "shadow"
    assert known["PUBLIC_DATA_FLOOR_DATE"] == "2026-04-23"


def test_collect_flags_extras_picks_up_unknown_prefix(monkeypatch):
    """An undeclared CRYPTO_*-prefixed var still surfaces — so a
    new operator flag added in code can never go silent."""
    for f in ops.KNOWN_FLAGS:
        monkeypatch.delenv(f, raising=False)
    monkeypatch.setenv("CRYPTO_FUTURE_FLAG_XYZ", "experimental")
    _known, extras = ops._collect_flags()
    assert extras.get("CRYPTO_FUTURE_FLAG_XYZ") == "experimental"


def test_collect_flags_extras_never_duplicates_known(monkeypatch):
    monkeypatch.setenv("CRYPTO_ADVERSARIAL_PHASE", "shadow")
    known, extras = ops._collect_flags()
    assert "CRYPTO_ADVERSARIAL_PHASE" in known
    assert "CRYPTO_ADVERSARIAL_PHASE" not in extras


# ── _collect_integrations ─────────────────────────────────────────


def test_collect_integrations_never_returns_key_value(monkeypatch):
    monkeypatch.setenv("STRIPE_API_KEY", "sk_live_topsecret")
    rows = ops._collect_integrations()
    serialised = str(rows)
    assert "sk_live_topsecret" not in serialised
    stripe_row = next(r for r in rows if r["env_var"] == "STRIPE_API_KEY")
    assert stripe_row["configured"] is True


def test_collect_integrations_unset_marks_unconfigured(monkeypatch):
    monkeypatch.delenv("USPTO_API_KEY", raising=False)
    rows = ops._collect_integrations()
    uspto = next(r for r in rows if r["env_var"] == "USPTO_API_KEY")
    assert uspto["configured"] is False


def test_collect_integrations_whitespace_only_unconfigured(monkeypatch):
    """An accidentally-blanked key (e.g. `STRIPE_API_KEY=  `) must
    register as NOT configured, not configured-with-empty-string."""
    monkeypatch.setenv("USPTO_API_KEY", "   ")
    rows = ops._collect_integrations()
    uspto = next(r for r in rows if r["env_var"] == "USPTO_API_KEY")
    assert uspto["configured"] is False


# ── _probe_mongo ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_probe_mongo_no_db_handle():
    out = await ops._probe_mongo(None)
    assert out == {"ok": False, "error": "db_handle_missing"}


@pytest.mark.asyncio
async def test_probe_mongo_db_without_client_attr():
    """Some unit tests pass a dict-like fake DB that lacks a
    `.client` attribute — must not crash."""
    class FakeDB: pass
    out = await ops._probe_mongo(FakeDB())
    assert out == {"ok": False, "error": "no_client_handle"}


@pytest.mark.asyncio
async def test_probe_mongo_happy_path():
    fake_client = MagicMock()
    fake_client.admin.command = AsyncMock(return_value={"ok": 1})

    class FakeDB:
        client = fake_client

    out = await ops._probe_mongo(FakeDB())
    assert out["ok"] is True
    assert "ping_ms" in out
    fake_client.admin.command.assert_awaited_once_with("ping")


@pytest.mark.asyncio
async def test_probe_mongo_command_raises():
    fake_client = MagicMock()
    fake_client.admin.command = AsyncMock(
        side_effect=ConnectionError("connection refused"),
    )

    class FakeDB:
        client = fake_client

    out = await ops._probe_mongo(FakeDB())
    assert out["ok"] is False
    assert "ConnectionError" in out["error"]


# ── _heuristic_notes ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_scheduler_heartbeat_prefers_dedicated_doc():
    """Pin the new contract: dedicated `scheduler_heartbeat` document
    is the source of truth. The old proxy-across-three-collections
    fallback is only consulted when the dedicated doc is missing."""
    now = datetime.now(timezone.utc)
    fresh = now - timedelta(seconds=30)
    proxy_old = now - timedelta(days=2)

    fake_collection = MagicMock()
    # Dedicated heartbeat — fresh.
    fake_collection.find_one = AsyncMock(return_value={
        "last_beat_at": fresh, "beat_count": 42, "host_pid": 1234,
    })

    class FakeDB:
        # Dispatch by collection name so we can prove the dedicated
        # doc short-circuits the proxy fallback.
        def __init__(self):
            self.calls = []

        def __getitem__(self, name):
            self.calls.append(name)
            if name == "scheduler_heartbeat":
                return fake_collection
            # Proxy collections — should NOT be consulted.
            stale = MagicMock()
            stale.find_one = AsyncMock(
                return_value={"opened_at": proxy_old, "logged_at": proxy_old},
            )
            return stale

        # Attribute-style access mirrors how server.py writes.
        @property
        def scheduler_heartbeat(self):
            return self["scheduler_heartbeat"]

    db = FakeDB()
    out = await ops._collect_scheduler_heartbeat(db)
    assert out["ok"] is True
    assert out["source"] == "dedicated_heartbeat"
    assert out["age_seconds"] < 60
    assert out["beat_count"] == 42
    # Proxy collections must not have been touched.
    assert "research_shadow_decisions" not in db.calls
    assert "crypto_paper_trades" not in db.calls
    assert "paper_trades" not in db.calls


@pytest.mark.asyncio
async def test_scheduler_heartbeat_falls_back_to_proxy_when_dedicated_missing():
    """First minute after a fresh deploy: the dedicated doc may not
    exist yet. Verify we still surface a useful answer from proxies."""
    now = datetime.now(timezone.utc)
    proxy_fresh = now - timedelta(minutes=10)

    class FakeDB:
        def __getitem__(self, name):
            coll = MagicMock()
            if name == "scheduler_heartbeat":
                coll.find_one = AsyncMock(return_value=None)
            elif name == "crypto_paper_trades":
                coll.find_one = AsyncMock(return_value={"opened_at": proxy_fresh})
            else:
                coll.find_one = AsyncMock(return_value=None)
            return coll

        @property
        def scheduler_heartbeat(self):
            return self["scheduler_heartbeat"]

    out = await ops._collect_scheduler_heartbeat(FakeDB())
    assert out["ok"] is True
    assert out["source"] == "proxy_fallback"
    assert out["age_seconds"] < 700  # ~10 min


@pytest.mark.asyncio
async def test_scheduler_heartbeat_stale_when_dedicated_doc_old():
    """The 3-minute staleness threshold is what catches a wedged
    scheduler — pin it so it doesn't get accidentally relaxed."""
    now = datetime.now(timezone.utc)
    stale = now - timedelta(minutes=10)

    fake_collection = MagicMock()
    fake_collection.find_one = AsyncMock(return_value={
        "last_beat_at": stale, "beat_count": 999,
    })

    class FakeDB:
        def __getitem__(self, _name):
            return fake_collection

        @property
        def scheduler_heartbeat(self):
            return fake_collection

    out = await ops._collect_scheduler_heartbeat(FakeDB())
    assert out["ok"] is False
    assert out["source"] == "dedicated_heartbeat"


def test_notes_all_good_when_everything_ok():
    flags = {f: None for f in ops.KNOWN_FLAGS}
    integrations = [
        {"env_var": e, "configured": True}
        for e in ("MONGO_URL", "EMERGENT_LLM_KEY", "STRIPE_API_KEY", "USPTO_API_KEY")
    ]
    notes = ops._heuristic_notes(
        flags, integrations,
        mongo={"ok": True}, scheduler={"ok": True},
        tier3={"tier3_unlocked": False},
    )
    assert notes == ["All gauges nominal."]


def test_notes_flags_mongo_failure():
    notes = ops._heuristic_notes(
        {f: None for f in ops.KNOWN_FLAGS},
        [{"env_var": "MONGO_URL", "configured": True}],
        mongo={"ok": False},
        scheduler={"ok": True},
        tier3={},
    )
    assert any("Mongo ping failed" in n for n in notes)


def test_notes_flags_scheduler_stalled():
    notes = ops._heuristic_notes(
        {f: None for f in ops.KNOWN_FLAGS}, [],
        mongo={"ok": True},
        scheduler={"ok": False, "age_seconds": 7200},
        tier3={},
    )
    assert any("scheduler appears stalled" in n.lower() for n in notes)


def test_notes_flags_uspto_missing(monkeypatch):
    notes = ops._heuristic_notes(
        {f: None for f in ops.KNOWN_FLAGS},
        [{"env_var": "USPTO_API_KEY", "configured": False}],
        mongo={"ok": True}, scheduler={"ok": True}, tier3={},
    )
    assert any("USPTO_API_KEY" in n for n in notes)


def test_notes_flags_invalid_adversarial_phase():
    flags = dict.fromkeys(ops.KNOWN_FLAGS)
    flags["CRYPTO_ADVERSARIAL_ENABLED"] = "1"
    flags["CRYPTO_ADVERSARIAL_PHASE"] = "bogus"
    notes = ops._heuristic_notes(
        flags, [{"env_var": "MONGO_URL", "configured": True}],
        mongo={"ok": True}, scheduler={"ok": True}, tier3={},
    )
    assert any("invalid" in n.lower() and "adversarial" in n.lower() for n in notes)


def test_notes_flags_modulator_on_but_tier3_locked():
    flags = dict.fromkeys(ops.KNOWN_FLAGS)
    flags["COUNCIL_RISK_MODULATOR_ENABLED"] = "true"
    notes = ops._heuristic_notes(
        flags, [{"env_var": "MONGO_URL", "configured": True}],
        mongo={"ok": True}, scheduler={"ok": True},
        tier3={"tier3_unlocked": False},
    )
    assert any("modulator" in n.lower() and "inert" in n.lower() for n in notes)


# ── collect_ops_snapshot integration ──────────────────────────────


@pytest.mark.asyncio
async def test_collect_ops_snapshot_stable_schema_with_no_db():
    out = await ops.collect_ops_snapshot(None)
    expected_top_level = {
        "generated_at", "runtime", "operator_flags", "extra_flags",
        "integrations", "mongo", "scheduler", "tier3", "notes",
    }
    assert set(out.keys()) == expected_top_level
    # No db → mongo + scheduler + tier3 each report their own error
    # but the response shape stays intact.
    assert out["mongo"]["ok"] is False
    assert out["scheduler"]["ok"] is False
    assert isinstance(out["notes"], list) and out["notes"]
