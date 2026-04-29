"""Dual-stack invariant tests — the CI safety net.

Asserts every rule in ``RISEDUAL_DUAL_STACK_SPEC.md`` §1–§7. Failures
here mean the firewall has been breached at the source level, not at
runtime — fix before merging.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from services.role_scoped_db import (
    DtdClient, PrdReadOnlyClient, BridgeCalibrationClient,
    DTD_COLLECTIONS, PRD_COLLECTIONS, BRIDGE_COLLECTIONS,
    _ReadOnlyCollection,
)
from services import firewall, dtd_replay_channel, promotion_bridge


BACKEND = Path("/app/backend")


# ── Collection-ownership invariants ──────────────────────────────────


def test_no_collection_in_two_domains():
    """A collection must belong to exactly one domain."""
    overlaps = (
        (DTD_COLLECTIONS & PRD_COLLECTIONS, "DTD ∩ PRD"),
        (DTD_COLLECTIONS & BRIDGE_COLLECTIONS, "DTD ∩ BRIDGE"),
        (PRD_COLLECTIONS & BRIDGE_COLLECTIONS, "PRD ∩ BRIDGE"),
    )
    for overlap, label in overlaps:
        assert not overlap, f"{label} overlap: {overlap}"


# ── Role-scoped DB capability invariants ─────────────────────────────


class _FakeDB:
    def __init__(self):
        self._calls = []

    def __getitem__(self, name):
        return _FakeCollection(name, self._calls)


class _FakeCollection:
    def __init__(self, name, log):
        self.name = name
        self._log = log

    async def insert_one(self, doc):
        self._log.append((self.name, "insert_one"))

    async def update_one(self, *a, **kw):
        self._log.append((self.name, "update_one"))

    def find(self, *a, **kw):
        self._log.append((self.name, "find"))
        return self


def test_dtd_client_denies_prd_collections():
    db = _FakeDB()
    c = DtdClient(db)
    for coll in PRD_COLLECTIONS | BRIDGE_COLLECTIONS:
        with pytest.raises(PermissionError):
            c[coll]


def test_dtd_client_allows_dtd_collections():
    db = _FakeDB()
    c = DtdClient(db)
    for coll in DTD_COLLECTIONS:
        assert c[coll].name == coll


def test_prd_client_readonly_on_dtd_collections():
    db = _FakeDB()
    c = PrdReadOnlyClient(db)
    for coll in DTD_COLLECTIONS:
        wrapper = c[coll]
        assert isinstance(wrapper, _ReadOnlyCollection)
        # Mutation methods must raise
        with pytest.raises(PermissionError):
            wrapper.insert_one({"x": 1})
        with pytest.raises(PermissionError):
            wrapper.update_one({}, {})


def test_prd_client_full_rw_on_prd_collections():
    db = _FakeDB()
    c = PrdReadOnlyClient(db)
    for coll in PRD_COLLECTIONS:
        assert c[coll].name == coll  # raw collection, not a wrapper


def test_prd_client_denies_bridge_collections():
    db = _FakeDB()
    c = PrdReadOnlyClient(db)
    for coll in BRIDGE_COLLECTIONS:
        with pytest.raises(PermissionError):
            c[coll]


def test_bridge_client_capabilities():
    db = _FakeDB()
    c = BridgeCalibrationClient(db)
    # write to BRIDGE
    assert c["bridge_activations"].name == "bridge_activations"
    # read PRD
    assert isinstance(c["ai_core_trades"], _ReadOnlyCollection)
    # deny DTD
    for coll in DTD_COLLECTIONS:
        with pytest.raises(PermissionError):
            c[coll]


# ── Firewall invariants ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_firewall_rejects_unsettled_publish(monkeypatch):
    db = AsyncMock()
    db.__getitem__ = lambda _self, name: AsyncMock()
    firewall.set_db(db)
    res = await firewall.publish_resolved(
        {
            "outcome_id": "x", "source": "paper_trades", "symbol": "AAA",
            "outcome": "win",
            "resolved_at": "2026-04-28T23:59:59+00:00",  # in the future
        },
        settle_seconds=86400,  # 24h required
    )
    assert res["ok"] is False
    assert "not_yet_settled" in res["reason"]


@pytest.mark.asyncio
async def test_firewall_rejects_missing_fields():
    res = await firewall.publish_resolved({"symbol": "AAA"}, settle_seconds=0)
    assert res["ok"] is False
    assert "missing_fields" in res["reason"]


# ── DTD replay invariants ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dtd_replay_rejects_missing_fields():
    res = await dtd_replay_channel.record_decision({})
    assert res["ok"] is False


# ── Promotion Bridge invariants ──────────────────────────────────────


def test_bridge_registry_empty_by_default():
    assert promotion_bridge.list_specs() == []


def test_bridge_get_calibration_returns_none_when_inactive():
    assert promotion_bridge.get_calibration("nonexistent") is None


@pytest.mark.asyncio
async def test_bridge_activate_requires_token(monkeypatch):
    monkeypatch.setenv("BRIDGE_APPROVAL_TOKEN", "expected-secret")
    spec = promotion_bridge.BridgeSpec(
        name="test_bridge", version="v1",
        output_target="conf_scale", output_bounds=(0.5, 1.5),
        min_samples=10, oos_window_days=3, regression_threshold=2.0,
    )
    promotion_bridge.register(spec)
    res = await promotion_bridge.activate(
        "test_bridge", value=1.1, approval_token="wrong",
        actor="test", evidence={
            "sample_count": 100, "oos_window_days": 7, "regression_pct": 0.5,
        },
    )
    assert res["ok"] is False
    assert res["reason"] == "invalid_approval_token"
    # Cleanup
    promotion_bridge._REGISTRY.pop("test_bridge", None)


@pytest.mark.asyncio
async def test_bridge_activate_enforces_bounds(monkeypatch):
    monkeypatch.setenv("BRIDGE_APPROVAL_TOKEN", "tok")
    spec = promotion_bridge.BridgeSpec(
        name="bounded_bridge", version="v1",
        output_target="conf_scale", output_bounds=(0.5, 1.5),
        min_samples=10, oos_window_days=3, regression_threshold=2.0,
    )
    promotion_bridge.register(spec)
    res = await promotion_bridge.activate(
        "bounded_bridge", value=999.0,  # way out of bounds
        approval_token="tok", actor="t",
        evidence={"sample_count": 100, "oos_window_days": 7, "regression_pct": 0.5},
    )
    assert res["ok"] is False
    assert "value_out_of_bounds" in res["reason"]
    promotion_bridge._REGISTRY.pop("bounded_bridge", None)


@pytest.mark.asyncio
async def test_bridge_activate_rejects_insufficient_evidence(monkeypatch):
    monkeypatch.setenv("BRIDGE_APPROVAL_TOKEN", "tok")
    spec = promotion_bridge.BridgeSpec(
        name="evidence_bridge", version="v1",
        output_target="conf_scale", output_bounds=(0.5, 1.5),
        min_samples=100, oos_window_days=7, regression_threshold=1.0,
    )
    promotion_bridge.register(spec)
    res = await promotion_bridge.activate(
        "evidence_bridge", value=1.1, approval_token="tok", actor="t",
        evidence={"sample_count": 5, "oos_window_days": 7, "regression_pct": 0.5},
    )
    assert res["ok"] is False
    assert "insufficient_samples" in res["reason"]
    promotion_bridge._REGISTRY.pop("evidence_bridge", None)


@pytest.mark.asyncio
async def test_bridge_round_trip(monkeypatch):
    monkeypatch.setenv("BRIDGE_APPROVAL_TOKEN", "tok")
    promotion_bridge._db = None  # skip Mongo writes
    spec = promotion_bridge.BridgeSpec(
        name="rt_bridge", version="v1",
        output_target="conf_scale", output_bounds=(0.5, 1.5),
        min_samples=10, oos_window_days=3, regression_threshold=2.0,
    )
    promotion_bridge.register(spec)
    assert promotion_bridge.get_calibration("rt_bridge") is None

    a = await promotion_bridge.activate(
        "rt_bridge", value=1.2, approval_token="tok", actor="op",
        evidence={"sample_count": 100, "oos_window_days": 7, "regression_pct": 0.5},
    )
    assert a["ok"] is True
    assert promotion_bridge.get_calibration("rt_bridge") == 1.2

    r = await promotion_bridge.revoke("rt_bridge", actor="op", reason="test")
    assert r["ok"] is True
    assert promotion_bridge.get_calibration("rt_bridge") is None
    promotion_bridge._REGISTRY.pop("rt_bridge", None)


# ── Domain-tag grep audit (advisory, not enforcement) ────────────────


_DOMAIN_RX = re.compile(r'^__domain__\s*=\s*"(DTD|PRD|BRIDGE)"', re.MULTILINE)


def _scan_domains() -> dict[str, set[Path]]:
    out: dict[str, set[Path]] = {"DTD": set(), "PRD": set(), "BRIDGE": set()}
    for p in (BACKEND / "services").rglob("*.py"):
        try:
            text = p.read_text()
        except (OSError, UnicodeDecodeError):
            continue
        m = _DOMAIN_RX.search(text)
        if m:
            out[m.group(1)].add(p)
    for p in (BACKEND / "routes").rglob("*.py"):
        try:
            text = p.read_text()
        except (OSError, UnicodeDecodeError):
            continue
        m = _DOMAIN_RX.search(text)
        if m:
            out[m.group(1)].add(p)
    return out


def test_domain_tagged_modules_exist():
    """Sanity check — at least the dual-stack modules we just shipped
    declare a __domain__. Backfilling all legacy modules is a separate
    cleanup; this test guards against accidental tag removal."""
    domains = _scan_domains()
    expected_bridge_files = {
        "firewall.py", "promotion_bridge.py", "role_scoped_db.py",
    }
    found = {p.name for p in domains["BRIDGE"]}
    missing = expected_bridge_files - found
    assert not missing, f"BRIDGE modules missing __domain__ tag: {missing}"


def test_no_prd_module_imports_dtd_module():
    """Grep-level audit: a module declaring __domain__ = "PRD" must not
    import any module declaring __domain__ = "DTD". Whitelist: a PRD
    module *may* read from dtd_replay_channel via the read_replay
    function (PRD-egress-only path), but never import it for write."""
    domains = _scan_domains()
    dtd_modnames = {p.stem for p in domains["DTD"]}
    if not dtd_modnames:
        pytest.skip("no DTD-tagged modules yet")
    violations: list[tuple[str, str]] = []
    for prd_file in domains["PRD"]:
        text = prd_file.read_text()
        for dtd_name in dtd_modnames:
            # Match `from services.<dtd_name> import …` and `import services.<dtd_name>`
            pattern = rf"\b(from\s+services\.{dtd_name}\s+import|import\s+services\.{dtd_name})\b"
            if re.search(pattern, text):
                # Allow read_replay-only consumption of dtd_replay_channel
                if dtd_name == "dtd_replay_channel" and "read_replay" in text and "record_decision" not in text:
                    continue
                violations.append((prd_file.name, dtd_name))
    assert not violations, f"PRD imports of DTD modules: {violations}"
