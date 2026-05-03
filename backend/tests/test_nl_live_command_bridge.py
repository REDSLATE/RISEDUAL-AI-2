"""
Tests for `services.nl_live_command_bridge`.

Pins the invariants of the two-step NL → live mutation bridge:

    1. ``prepare_nl_live_command`` stages a pending row but does NOT mutate
       any downstream config (integrity_mitigations, confidence_gate_overrides).
    2. Integrity-layer ceiling wins on SET_RISK_MULTIPLIER preparations —
       the NL layer can tighten risk but cannot widen it above the
       deterministic throttle floor.
    3. ``confirm_nl_live_command`` routes SET_RISK_MULTIPLIER through
       ``activate_integrity_mitigation`` (never bypasses it).
    4. ``confirm_nl_live_command`` routes SET_MIN_RR into the
       ``confidence_gate_overrides`` doc with ``_id="current"``.
    5. Confirm is idempotent — a second confirm returns ``already_applied``
       without double-inserting mitigations / overrides.
    6. Expired pending rows refuse to confirm.
    7. Session-mismatch refuses to confirm — only the session that prepared
       can apply.
    8. ``confidence_gate.get_dynamic_confidence_threshold`` reads the
       ``confidence_gate_overrides._id="current"`` doc and shifts the base
       threshold when an active override is present, and ignores it when
       expired.

Uses a minimal in-memory async Mongo stub so the suite stays hermetic.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest


# ── In-memory async Mongo stub ──────────────────────────────────────


class _FakeCursor:
    def __init__(self, rows):
        self._rows = list(rows)

    def sort(self, *_args, **_kwargs):
        return self

    async def to_list(self, length=None):  # noqa: ARG002
        return list(self._rows)


class _FakeCollection:
    def __init__(self):
        self._docs: list[dict] = []
        self._next_id = 1

    async def insert_one(self, doc):
        doc.setdefault("_id", self._next_id)
        self._next_id += 1
        self._docs.append(doc)
        return type("R", (), {"inserted_id": doc["_id"]})()

    async def find_one(self, query, projection=None):  # noqa: ARG002
        for d in self._docs:
            if all(d.get(k) == v for k, v in query.items()):
                out = dict(d)
                if projection:
                    # respect only the simple exclusion form used by the bridge
                    if projection.get("_id") == 0:
                        out.pop("_id", None)
                return out
        return None

    async def update_one(self, query, update, upsert=False):
        set_ = update.get("$set") or {}
        for d in self._docs:
            if all(d.get(k) == v for k, v in query.items()):
                for k, v in set_.items():
                    d[k] = v
                return type("R", (), {"modified_count": 1, "upserted_id": None})()
        if upsert:
            new_doc = {**query, **set_}
            await self.insert_one(new_doc)
            return type("R", (), {"modified_count": 0, "upserted_id": new_doc.get("_id")})()
        return type("R", (), {"modified_count": 0, "upserted_id": None})()

    async def update_many(self, query, update):
        active = query.get("active")
        expires_lte = (query.get("expires_at") or {}).get("$lte")
        set_ = update.get("$set") or {}
        modified = 0
        for d in self._docs:
            if active is not None and d.get("active") is not active:
                continue
            if expires_lte is not None and d.get("expires_at") > expires_lte:
                continue
            for k, v in set_.items():
                d[k] = v
            modified += 1
        return type("R", (), {"modified_count": modified})()

    def find(self, query):
        active = query.get("active")
        expires_gt = (query.get("expires_at") or {}).get("$gt")
        rows = []
        for d in self._docs:
            if active is not None and d.get("active") is not active:
                continue
            if expires_gt is not None and d.get("expires_at") <= expires_gt:
                continue
            rows.append(d)
        return _FakeCursor(rows)

    async def create_index(self, *args, **kwargs):  # noqa: ARG002
        return "idx_ok"


class _FakeDB:
    def __init__(self):
        self._cols: dict[str, _FakeCollection] = {}

    def __getitem__(self, name: str) -> _FakeCollection:
        if name not in self._cols:
            self._cols[name] = _FakeCollection()
        return self._cols[name]

    def __getattr__(self, name: str) -> _FakeCollection:
        # integrity_mitigation_service uses `db.integrity_mitigations`
        # (attribute access). Route to the same backing store as the
        # bracket-access path so the two-step flow sees one collection.
        if name.startswith("_"):
            raise AttributeError(name)
        return self[name]


@pytest.fixture
def db():
    return _FakeDB()


# ── Test 1 — prepare stages, does not mutate downstream ─────────────


@pytest.mark.asyncio
async def test_prepare_stages_pending_row_without_side_effects(db):
    from services.nl_live_command_bridge import prepare_nl_live_command

    out = await prepare_nl_live_command(
        db,
        kind="SET_RISK_MULTIPLIER",
        value=0.75,
        original_text="reduce risk to 0.75",
        session_id="sess-abc123",
    )

    assert out["ok"] is True
    assert out["kind"] == "SET_RISK_MULTIPLIER"
    assert out["requested_value"] == 0.75
    assert out["effective_value"] == 0.75
    assert out["floor_note"] is None
    assert "pending_id" in out

    # Pending row exists with status=pending
    pending = await db["nl_pending_commands"].find_one(
        {"pending_id": out["pending_id"]}, {"_id": 0}
    )
    assert pending is not None
    assert pending["status"] == "pending"

    # Downstream side-effect collections are untouched.
    assert db["integrity_mitigations"]._docs == []
    assert db["confidence_gate_overrides"]._docs == []


# ── Test 2 — integrity ceiling caps the requested risk multiplier ──


@pytest.mark.asyncio
async def test_prepare_caps_risk_multiplier_to_integrity_ceiling(db):
    from services.integrity_mitigation_service import activate_integrity_mitigation
    from services.nl_live_command_bridge import prepare_nl_live_command

    # Pre-throttle: integrity layer has clamped sizing to 0.70×.
    await activate_integrity_mitigation(
        db,
        source_rule_id="pre-existing-throttle",
        mitigation={"action": "DEGRADE_TRADING", "params": {"position_multiplier": 0.70}},
        ttl_minutes=60,
    )

    # Operator asks to BUMP to 1.25× — must cap at 0.70×.
    out = await prepare_nl_live_command(
        db,
        kind="SET_RISK_MULTIPLIER",
        value=1.25,
        original_text="raise risk to 1.25",
        session_id="sess-xyz",
    )
    assert out["ok"] is True
    assert out["requested_value"] == 1.25
    assert out["effective_value"] == 0.70
    assert out["floor_note"] is not None
    assert "Capped by integrity" in out["floor_note"]


# ── Test 3 — confirm SET_RISK_MULTIPLIER routes through integrity ──


@pytest.mark.asyncio
async def test_confirm_risk_multiplier_activates_integrity_mitigation(db):
    from services.integrity_mitigation_service import (
        get_effective_integrity_risk_multiplier,
    )
    from services.nl_live_command_bridge import (
        confirm_nl_live_command,
        prepare_nl_live_command,
    )

    prep = await prepare_nl_live_command(
        db,
        kind="SET_RISK_MULTIPLIER",
        value=0.80,
        original_text="trim risk",
        session_id="sess-1",
    )
    assert prep["ok"] is True

    conf = await confirm_nl_live_command(
        db, pending_id=prep["pending_id"], session_id="sess-1"
    )
    assert conf["ok"] is True
    assert conf["apply_result"]["applied"] is True
    assert conf["apply_result"]["effective_value"] == 0.80

    # Integrity collection shows the new row.
    active_mult = await get_effective_integrity_risk_multiplier(db)
    assert active_mult == 0.80


# ── Test 4 — confirm SET_MIN_RR writes the override doc ─────────────


@pytest.mark.asyncio
async def test_confirm_min_rr_writes_confidence_gate_override(db):
    from services.nl_live_command_bridge import (
        confirm_nl_live_command,
        prepare_nl_live_command,
    )

    prep = await prepare_nl_live_command(
        db,
        kind="SET_MIN_RR",
        value=2.0,
        original_text="tighten r:r to 2",
        session_id="sess-rr",
    )
    conf = await confirm_nl_live_command(
        db, pending_id=prep["pending_id"], session_id="sess-rr"
    )
    assert conf["ok"] is True
    assert conf["apply_result"]["effective_value"] == 2.0

    override = await db["confidence_gate_overrides"].find_one(
        {"_id": "current"}, {"_id": 0}
    )
    assert override is not None
    assert override["min_rr"] == 2.0
    assert override["set_by"].startswith("nl_command:")
    # Expiry is in the future.
    assert override["expires_at"] > datetime.now(timezone.utc)


# ── Test 5 — confirm is idempotent ─────────────────────────────────


@pytest.mark.asyncio
async def test_confirm_is_idempotent_on_retry(db):
    from services.nl_live_command_bridge import (
        confirm_nl_live_command,
        prepare_nl_live_command,
    )

    prep = await prepare_nl_live_command(
        db,
        kind="SET_MIN_RR",
        value=1.8,
        original_text="raise rr to 1.8",
        session_id="sess-idem",
    )
    first = await confirm_nl_live_command(
        db, pending_id=prep["pending_id"], session_id="sess-idem"
    )
    assert first["ok"] is True

    # Re-confirm — must not double-apply.
    second = await confirm_nl_live_command(
        db, pending_id=prep["pending_id"], session_id="sess-idem"
    )
    assert second["ok"] is True
    assert second.get("already_applied") is True

    # Only ONE override row (update_one upsert on fixed _id).
    docs = db["confidence_gate_overrides"]._docs
    assert len(docs) == 1


# ── Test 6 — expired pending row refuses to confirm ────────────────


@pytest.mark.asyncio
async def test_expired_pending_refuses_to_confirm(db):
    from services.nl_live_command_bridge import (
        confirm_nl_live_command,
        prepare_nl_live_command,
    )

    prep = await prepare_nl_live_command(
        db,
        kind="SET_MIN_RR",
        value=1.75,
        original_text="rr 1.75",
        session_id="sess-exp",
    )
    # Back-date the pending row past its expires_at.
    pending_id = prep["pending_id"]
    for d in db["nl_pending_commands"]._docs:
        if d.get("pending_id") == pending_id:
            d["expires_at"] = datetime.now(timezone.utc) - timedelta(seconds=1)

    out = await confirm_nl_live_command(
        db, pending_id=pending_id, session_id="sess-exp"
    )
    assert out["ok"] is False
    assert out["error"] == "pending_expired"

    # No override was written.
    assert db["confidence_gate_overrides"]._docs == []


# ── Test 7 — session mismatch refuses to confirm ───────────────────


@pytest.mark.asyncio
async def test_session_mismatch_refuses_to_confirm(db):
    from services.nl_live_command_bridge import (
        confirm_nl_live_command,
        prepare_nl_live_command,
    )

    prep = await prepare_nl_live_command(
        db,
        kind="SET_MIN_RR",
        value=2.0,
        original_text="rr 2",
        session_id="sess-owner",
    )
    out = await confirm_nl_live_command(
        db, pending_id=prep["pending_id"], session_id="sess-attacker"
    )
    assert out["ok"] is False
    assert out["error"] == "session_mismatch"
    assert db["confidence_gate_overrides"]._docs == []


# ── Test 8 — unknown pending_id ────────────────────────────────────


@pytest.mark.asyncio
async def test_unknown_pending_id_returns_not_found(db):
    from services.nl_live_command_bridge import confirm_nl_live_command

    out = await confirm_nl_live_command(
        db, pending_id="does-not-exist", session_id="sess-x"
    )
    assert out["ok"] is False
    assert out["error"] == "pending_not_found"


# ── Test 9 — confidence_gate consumes the active override ──────────


@pytest.mark.asyncio
async def test_confidence_gate_reads_active_override(db, monkeypatch):
    """When an active override with min_rr > 1.5 lives in
    ``confidence_gate_overrides``, the dynamic threshold base lifts by a
    controlled amount (operator spec: extra_rr × 0.10, capped at 0.15)."""
    from services import confidence_gate as gate

    # Pre-seed an active override (equivalent to a confirmed SET_MIN_RR).
    now = datetime.now(timezone.utc)
    await db["confidence_gate_overrides"].update_one(
        {"_id": "current"},
        {"$set": {
            "min_rr": 2.5,
            "set_by": "nl_command:test",
            "set_at": now,
            "expires_at": now + timedelta(minutes=30),
            "original_text": "tight rr",
        }},
        upsert=True,
    )

    # Stub out the track-record + calibration probes so we isolate the
    # override-only contribution to the threshold.
    async def _zero_track(_db, *, asset_type):  # noqa: ARG001
        return 0.0, 0

    async def _zero_cal(_db, *, asset_type):  # noqa: ARG001
        return 0.0

    monkeypatch.setattr(gate, "_read_drawdown_and_loss_streak", _zero_track)
    monkeypatch.setattr(gate, "_read_calibration_gap", _zero_cal)

    result = await gate.get_dynamic_confidence_threshold(db, asset_type="equity")
    # Base 0.70 + min(0.15, (2.5 - 1.5) * 0.10) = 0.70 + 0.10 = 0.80
    assert result.base == pytest.approx(0.80, abs=0.001)
    assert result.threshold == pytest.approx(0.80, abs=0.001)


@pytest.mark.asyncio
async def test_confidence_gate_ignores_expired_override(db, monkeypatch):
    from services import confidence_gate as gate

    now = datetime.now(timezone.utc)
    await db["confidence_gate_overrides"].update_one(
        {"_id": "current"},
        {"$set": {
            "min_rr": 3.0,
            "set_by": "nl_command:test",
            "set_at": now - timedelta(hours=3),
            "expires_at": now - timedelta(minutes=5),  # already expired
            "original_text": "stale",
        }},
        upsert=True,
    )

    async def _zero_track(_db, *, asset_type):  # noqa: ARG001
        return 0.0, 0

    async def _zero_cal(_db, *, asset_type):  # noqa: ARG001
        return 0.0

    monkeypatch.setattr(gate, "_read_drawdown_and_loss_streak", _zero_track)
    monkeypatch.setattr(gate, "_read_calibration_gap", _zero_cal)

    result = await gate.get_dynamic_confidence_threshold(db, asset_type="equity")
    # Expired override must be ignored — base falls back to BASE_MIN_CONFIDENCE.
    assert result.base == pytest.approx(gate.BASE_MIN_CONFIDENCE, abs=0.001)
    assert result.threshold == pytest.approx(gate.BASE_MIN_CONFIDENCE, abs=0.001)


# ── Test 10 — ensure_nl_live_indexes is resilient ──────────────────


@pytest.mark.asyncio
async def test_ensure_indexes_on_none_db_no_crash():
    from services.nl_live_command_bridge import ensure_nl_live_indexes

    assert (await ensure_nl_live_indexes(None))["status"] == "no_db"


@pytest.mark.asyncio
async def test_ensure_indexes_on_fake_db_succeeds(db):
    from services.nl_live_command_bridge import ensure_nl_live_indexes

    out = await ensure_nl_live_indexes(db)
    assert out["status"] == "ok"


# ── Test 11 — no-db path on prepare/confirm returns structured error


@pytest.mark.asyncio
async def test_no_db_path_returns_structured_error():
    from services.nl_live_command_bridge import (
        confirm_nl_live_command,
        prepare_nl_live_command,
    )

    prep = await prepare_nl_live_command(
        None,
        kind="SET_MIN_RR",
        value=2.0,
        original_text="",
        session_id="s",
    )
    assert prep == {"ok": False, "error": "no_db"}

    conf = await confirm_nl_live_command(
        None, pending_id="x", session_id="s"
    )
    assert conf == {"ok": False, "error": "no_db"}
