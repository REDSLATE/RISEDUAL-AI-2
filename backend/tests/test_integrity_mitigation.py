"""
Tests for the integrity-driven trading mitigation self-defense layer.

These pin the four invariants the user specified in the mitigation
spec:

    1. Multiplier defaults to 1.0 when no mitigation is active —
       this is the "cold start / quiet path" case that must never
       regress, because it's the path 99.99% of trades travel.
    2. Activation clamps the multiplier to the configured value,
       persists an audit row, and honours its TTL (a TTL'd record
       is flipped to inactive on the next read without being
       deleted).
    3. ``should_suppress_strong_signals`` only returns True when an
       ACTIVE DEGRADE_TRADING row has the explicit opt-in flag —
       silence by default is the non-negotiable contract.
    4. Param clamping: out-of-range / non-numeric multipliers are
       clamped to [0.25, 1.0]; the mitigation is about *reducing*
       exposure, not cancelling the strategy.

The tests use an in-memory Mongo-like stub so they run in <1s and
have no external dependencies.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from services.integrity_mitigation_service import (
    _clamp_multiplier,
    activate_integrity_mitigation,
    expire_integrity_mitigations,
    get_active_integrity_mitigations,
    get_effective_integrity_risk_multiplier,
    should_suppress_strong_signals,
    summarize_integrity_mitigation_state,
)


# ── In-memory Mongo-like stub ────────────────────────────────────


class _FakeCursor:
    def __init__(self, rows):
        self._rows = list(rows)

    def sort(self, *_args, **_kwargs):
        return self

    async def to_list(self, length=None):  # noqa: ARG002
        return list(self._rows)


class _FakeCollection:
    """Minimal async stand-in for a Motor collection.

    Supports the four operations `integrity_mitigation_service`
    exercises: ``insert_one``, ``update_many``, ``find`` with
    ``sort`` + ``to_list``.
    """

    def __init__(self):
        self._docs: list[dict] = []
        self._next_id = 1

    async def insert_one(self, doc):
        doc["_id"] = self._next_id
        self._next_id += 1
        self._docs.append(doc)
        return type("R", (), {"inserted_id": doc["_id"]})()

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


class _FakeDB:
    def __init__(self):
        self.integrity_mitigations = _FakeCollection()


# ── Fixtures ─────────────────────────────────────────────────────


@pytest.fixture
def db():
    return _FakeDB()


# ── Test 1 — multiplier defaults to 1.0 when nothing active ──────


@pytest.mark.asyncio
async def test_integrity_multiplier_defaults_to_one_when_no_mitigation_active(db):
    """Empty collection → 1.0 (no-op). Guarantees position sizing is
    untouched on the quiet path."""
    assert await get_effective_integrity_risk_multiplier(db) == 1.0
    assert await should_suppress_strong_signals(db) is False
    summary = await summarize_integrity_mitigation_state(db)
    assert summary["active"] is False
    assert summary["active_count"] == 0
    assert summary["risk_multiplier"] == 1.0
    assert summary["suppress_strong_signals"] is False
    assert summary["items"] == []


# ── Test 2 — activation clamps + TTL expiration ──────────────────


@pytest.mark.asyncio
async def test_activate_clamps_multiplier_and_expires_via_ttl(db):
    """Activating stores a record and clamps the multiplier. When
    the TTL elapses the row is flipped to inactive (never deleted)
    and the effective multiplier returns to 1.0."""
    mid = await activate_integrity_mitigation(
        db,
        source_rule_id="udt-rule-1",
        mitigation={
            "action": "DEGRADE_TRADING",
            "params": {"position_multiplier": 0.5},
        },
        ttl_minutes=30,
    )
    assert mid is not None

    # While active, the effective multiplier reflects the clamp.
    assert await get_effective_integrity_risk_multiplier(db) == 0.5
    active = await get_active_integrity_mitigations(db)
    assert len(active) == 1
    assert active[0]["source_rule_id"] == "udt-rule-1"
    assert active[0]["active"] is True

    # Simulate TTL elapse by back-dating expires_at.
    past = datetime.now(timezone.utc) - timedelta(minutes=1)
    active[0]["expires_at"] = past

    expired = await expire_integrity_mitigations(db)
    assert expired == 1
    # Audit trail preserved — row still exists but inactive.
    assert len(db.integrity_mitigations._docs) == 1
    assert db.integrity_mitigations._docs[0]["active"] is False
    assert db.integrity_mitigations._docs[0].get("expired_reason") == "ttl_elapsed"

    # Effective multiplier is back to 1.0 — no active rows anymore.
    assert await get_effective_integrity_risk_multiplier(db) == 1.0
    assert await should_suppress_strong_signals(db) is False


# ── Test 3 — strong-signal suppression is explicit opt-in ────────


@pytest.mark.asyncio
async def test_suppress_strong_signals_flag_requires_explicit_opt_in(db):
    """Activating WITHOUT the flag must not suppress strong signals
    — silence by default is the non-negotiable contract. Opting in
    flips the suppressor to True. Most-conservative logic: ANY
    active row with the flag enables suppression."""
    # First mitigation — flag off. Suppression must stay False.
    await activate_integrity_mitigation(
        db,
        source_rule_id="rule-no-flag",
        mitigation={
            "action": "DEGRADE_TRADING",
            "params": {"position_multiplier": 0.75},
        },
        ttl_minutes=30,
    )
    assert await should_suppress_strong_signals(db) is False
    assert await get_effective_integrity_risk_multiplier(db) == 0.75

    # Second mitigation — flag on. Now suppression must be True.
    await activate_integrity_mitigation(
        db,
        source_rule_id="rule-with-flag",
        mitigation={
            "action": "DEGRADE_TRADING",
            "params": {
                "position_multiplier": 0.6,
                "disable_strong_signals": True,
            },
        },
        ttl_minutes=30,
    )
    assert await should_suppress_strong_signals(db) is True
    # Most-conservative wins: effective multiplier is the min.
    assert await get_effective_integrity_risk_multiplier(db) == 0.6

    summary = await summarize_integrity_mitigation_state(db)
    assert summary["active"] is True
    assert summary["active_count"] == 2
    assert summary["suppress_strong_signals"] is True
    assert summary["risk_multiplier"] == 0.6


# ── Test 4 — param clamping + non-DEGRADE actions rejected ───────


@pytest.mark.asyncio
async def test_multiplier_clamps_to_safe_range(db):
    """Out-of-range + garbage multipliers clamp to [0.25, 1.0]. A
    mitigation is about reducing exposure, not cancelling the
    strategy — we never degrade below 0.25 and we never amplify
    above 1.0."""
    # Clamp helper — direct unit checks.
    assert _clamp_multiplier(0.1) == 0.25   # floor
    assert _clamp_multiplier(-1.0) == 0.25  # negatives floor
    assert _clamp_multiplier(0.5) == 0.5    # valid passes through
    assert _clamp_multiplier(2.0) == 1.0    # ceiling
    assert _clamp_multiplier("garbage") == 1.0  # falls back no-op
    assert _clamp_multiplier(None) == 1.0

    # Activation path mirrors the clamp.
    await activate_integrity_mitigation(
        db,
        source_rule_id="rule-too-aggressive",
        mitigation={
            "action": "DEGRADE_TRADING",
            "params": {"position_multiplier": 0.05},  # below floor
        },
        ttl_minutes=30,
    )
    assert await get_effective_integrity_risk_multiplier(db) == 0.25

    # Unsupported action is a no-op — returns None, does not insert.
    pre = len(db.integrity_mitigations._docs)
    result = await activate_integrity_mitigation(
        db,
        source_rule_id="rule-bad-action",
        mitigation={"action": "HALT_EVERYTHING"},
        ttl_minutes=30,
    )
    assert result is None
    assert len(db.integrity_mitigations._docs) == pre
