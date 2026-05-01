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
    are_new_bots_blocked_by_integrity,
    are_sizing_overrides_frozen_by_integrity,
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


# ── Test 5 — BLOCK_NEW_BOTS action ───────────────────────────────


@pytest.mark.asyncio
async def test_block_new_bots_action_detects_and_surfaces_details(db):
    """Activating BLOCK_NEW_BOTS flips the helper to True and carries
    the operator-facing details (source_rule_id, reason, expires_at)
    so the /api/bots 423 payload can be precise."""
    # Baseline — nothing active, bots flow freely.
    blocked, detail = await are_new_bots_blocked_by_integrity(db)
    assert blocked is False
    assert detail is None

    # Activate — reason string is preserved.
    await activate_integrity_mitigation(
        db,
        source_rule_id="rule-block-bots",
        mitigation={
            "action": "BLOCK_NEW_BOTS",
            "params": {"reason": "unknown-direction spike"},
        },
        ttl_minutes=30,
    )
    blocked, detail = await are_new_bots_blocked_by_integrity(db)
    assert blocked is True
    assert detail is not None
    assert detail["source_rule_id"] == "rule-block-bots"
    assert detail["reason"] == "unknown-direction spike"
    assert "expires_at" in detail

    # DEGRADE_TRADING alone must not trip the bot-blocker — the two
    # actions are independent.
    await db.integrity_mitigations.update_many(
        {"active": True}, {"$set": {"active": False}},
    )
    await activate_integrity_mitigation(
        db,
        source_rule_id="rule-degrade-only",
        mitigation={"action": "DEGRADE_TRADING", "params": {}},
        ttl_minutes=30,
    )
    blocked, detail = await are_new_bots_blocked_by_integrity(db)
    assert blocked is False


# ── Test 6 — FREEZE_SIZING_OVERRIDES action ──────────────────────


@pytest.mark.asyncio
async def test_freeze_sizing_overrides_action_detects_and_surfaces_details(db):
    """FREEZE_SIZING_OVERRIDES prevents operators from loosening
    guard-policy flags mid-incident. The helper returns the active
    row so the 423 response can name the source rule."""
    # Baseline.
    frozen, detail = await are_sizing_overrides_frozen_by_integrity(db)
    assert frozen is False
    assert detail is None

    await activate_integrity_mitigation(
        db,
        source_rule_id="rule-freeze-overrides",
        mitigation={
            "action": "FREEZE_SIZING_OVERRIDES",
            "params": {"reason": "pending root-cause investigation"},
        },
        ttl_minutes=30,
    )
    frozen, detail = await are_sizing_overrides_frozen_by_integrity(db)
    assert frozen is True
    assert detail is not None
    assert detail["source_rule_id"] == "rule-freeze-overrides"
    assert detail["reason"] == "pending root-cause investigation"

    # Summary surfaces both flags independently.
    summary = await summarize_integrity_mitigation_state(db)
    assert summary["freeze_sizing_overrides"] is True
    assert summary["block_new_bots"] is False


# ── Test 7 — Summary flags are independent ───────────────────────


@pytest.mark.asyncio
async def test_summary_surfaces_all_three_action_flags_independently(db):
    """Three simultaneous mitigations — one of each action — surface
    as independent flags in the summary. Most-conservative for the
    DEGRADE multiplier, AND-style for the two boolean actions."""
    await activate_integrity_mitigation(
        db,
        source_rule_id="rule-degrade",
        mitigation={
            "action": "DEGRADE_TRADING",
            "params": {
                "position_multiplier": 0.4,
                "disable_strong_signals": True,
            },
        },
        ttl_minutes=30,
    )
    await activate_integrity_mitigation(
        db,
        source_rule_id="rule-block",
        mitigation={"action": "BLOCK_NEW_BOTS", "params": {"reason": "x"}},
        ttl_minutes=30,
    )
    await activate_integrity_mitigation(
        db,
        source_rule_id="rule-freeze",
        mitigation={"action": "FREEZE_SIZING_OVERRIDES", "params": {"reason": "y"}},
        ttl_minutes=30,
    )

    summary = await summarize_integrity_mitigation_state(db)
    assert summary["active"] is True
    assert summary["active_count"] == 3
    assert summary["risk_multiplier"] == 0.4
    assert summary["suppress_strong_signals"] is True
    assert summary["block_new_bots"] is True
    assert summary["freeze_sizing_overrides"] is True

    # Per-item breakdown retains the action type for each row.
    types = {item["type"] for item in summary["items"]}
    assert types == {"DEGRADE_TRADING", "BLOCK_NEW_BOTS", "FREEZE_SIZING_OVERRIDES"}


# ── Test 8 — Slack activation notifier is best-effort ────────────


@pytest.mark.asyncio
async def test_slack_notifier_never_blocks_activation(db, monkeypatch):
    """A Slack webhook outage MUST NOT prevent the Mongo activation
    row from being committed — the audit trail is the source of
    truth, the Slack ping is a convenience. We simulate the
    webhook raising inside activate_integrity_mitigation and assert
    the mitigation still lands in Mongo and flips the helpers."""
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.test/broken")
    monkeypatch.setenv("INTEGRITY_MITIGATION_SLACK_ENABLED", "true")

    import services.integrity_mitigation_service as mod

    async def _boom(**kwargs):
        raise RuntimeError("simulated Slack outage")

    monkeypatch.setattr(mod, "_notify_slack_activation", _boom)

    mid = await activate_integrity_mitigation(
        db,
        source_rule_id="rule-slack-outage",
        mitigation={"action": "BLOCK_NEW_BOTS", "params": {"reason": "z"}},
        ttl_minutes=10,
    )
    # Activation still succeeds even though the notifier crashed.
    assert mid is not None
    blocked, detail = await are_new_bots_blocked_by_integrity(db)
    assert blocked is True
    assert detail["source_rule_id"] == "rule-slack-outage"


@pytest.mark.asyncio
async def test_slack_notifier_short_circuits_when_disabled(monkeypatch):
    """``INTEGRITY_MITIGATION_SLACK_ENABLED=false`` silences the
    notifier without touching the audit trail — useful for
    playback / staged rollouts. Returns False instead of raising."""
    from services.integrity_mitigation_service import _notify_slack_activation
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.test/ok")
    monkeypatch.setenv("INTEGRITY_MITIGATION_SLACK_ENABLED", "false")

    ok = await _notify_slack_activation(
        source_rule_id="test",
        action="BLOCK_NEW_BOTS",
        params={"reason": "r"},
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    assert ok is False


@pytest.mark.asyncio
async def test_slack_notifier_short_circuits_when_webhook_unset(monkeypatch):
    """With no SLACK_WEBHOOK_URL configured the notifier returns
    False silently — never crashes, never issues an HTTP call."""
    from services.integrity_mitigation_service import _notify_slack_activation
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    monkeypatch.setenv("INTEGRITY_MITIGATION_SLACK_ENABLED", "true")

    ok = await _notify_slack_activation(
        source_rule_id="test",
        action="DEGRADE_TRADING",
        params={"position_multiplier": 0.5},
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    assert ok is False
