"""Calibration Kanban tests (read-only — no DB mutations from tile path)."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

from services.calibration_kanban import (
    _build_checklist,
    _derive_phase_and_state,
    _enforce_flag_for,
    ChecklistItem,
    get_kanban,
)


# ── Pure logic ────────────────────────────────────────────────────


def _zero_metrics():
    return (
        {"total": 0, "approved": 0, "no_trade": 0, "by_stage": {}, "false_blocks": 0},
        {"total": 0, "by_decision": {"PASS": 0, "REDUCE": 0, "BLOCK": 0},
         "lane_mismatch": 0, "broker_health_blocks": 0,
         "exposure_cap_blocks": 0, "duplicate_symbol_blocks": 0,
         "cross_contamination": 0},
    )


def test_empty_lane_renders_cleanly():
    """A freshly-deployed lane with zero receipts should produce a
    valid checklist (min_receipts fails; everything else passes or
    is n/a) and resolve to phase=Shadow, state=Blocked."""
    dec, rg = _zero_metrics()
    checklist = _build_checklist(
        lane="equity",
        expected_collection="roadguard_equity_decisions",
        rg_collection="roadguard_equity_decisions",
        decision_metrics=dec,
        rg_metrics=rg,
        enforce_flag=False,
    )
    # All 6 checklist items present.
    keys = [c.key for c in checklist]
    assert keys == [
        "min_receipts", "zero_contamination", "zero_false_blocks",
        "broker_health_stable", "correct_collection", "enforce_off",
    ]
    # min_receipts fails on zero.
    assert next(c for c in checklist if c.key == "min_receipts").status == "fail"
    # broker_health_stable is n/a (no RG decisions yet).
    assert next(c for c in checklist if c.key == "broker_health_stable").status == "n/a"
    # collection match passes.
    assert next(c for c in checklist if c.key == "correct_collection").status == "pass"
    # enforce_off passes (flag is False).
    assert next(c for c in checklist if c.key == "enforce_off").status == "pass"

    phase, state, blockers = _derive_phase_and_state(
        checklist=checklist, enforce_flag=False,
    )
    assert phase == "Shadow"
    assert state == "Blocked"
    assert "min_receipts" in blockers


def test_full_pass_yields_ready_for_review():
    dec = {"total": 500, "approved": 100, "no_trade": 400,
           "by_stage": {"perception": 200}, "false_blocks": 0}
    rg = {"total": 250, "by_decision": {"PASS": 240, "REDUCE": 5, "BLOCK": 5},
          "lane_mismatch": 0, "broker_health_blocks": 1,
          "exposure_cap_blocks": 4, "duplicate_symbol_blocks": 0,
          "cross_contamination": 0}
    checklist = _build_checklist(
        lane="equity",
        expected_collection="roadguard_equity_decisions",
        rg_collection="roadguard_equity_decisions",
        decision_metrics=dec, rg_metrics=rg, enforce_flag=False,
    )
    phase, state, blockers = _derive_phase_and_state(
        checklist=checklist, enforce_flag=False,
    )
    assert phase == "Calibrate"
    assert state == "Ready for Review"
    assert blockers == []


def test_lane_contamination_blocks_promotion():
    dec = {"total": 500, "approved": 100, "no_trade": 400, "by_stage": {}, "false_blocks": 0}
    rg = {"total": 100, "by_decision": {"PASS": 99, "REDUCE": 0, "BLOCK": 1},
          "lane_mismatch": 1, "broker_health_blocks": 0,
          "exposure_cap_blocks": 0, "duplicate_symbol_blocks": 0,
          "cross_contamination": 1}  # ← contamination
    checklist = _build_checklist(
        lane="equity",
        expected_collection="roadguard_equity_decisions",
        rg_collection="roadguard_equity_decisions",
        decision_metrics=dec, rg_metrics=rg, enforce_flag=False,
    )
    item = next(c for c in checklist if c.key == "zero_contamination")
    assert item.status == "fail"
    phase, state, blockers = _derive_phase_and_state(
        checklist=checklist, enforce_flag=False,
    )
    assert state == "Blocked"
    assert "zero_contamination" in blockers


def test_false_blocks_block_promotion():
    dec = {"total": 500, "approved": 100, "no_trade": 400, "by_stage": {},
           "false_blocks": 3}  # ← operator marked 3 NO_TRADEs as wrong
    rg = {"total": 100, "by_decision": {"PASS": 99, "REDUCE": 0, "BLOCK": 1},
          "lane_mismatch": 0, "broker_health_blocks": 0,
          "exposure_cap_blocks": 0, "duplicate_symbol_blocks": 0,
          "cross_contamination": 0}
    checklist = _build_checklist(
        lane="equity",
        expected_collection="roadguard_equity_decisions",
        rg_collection="roadguard_equity_decisions",
        decision_metrics=dec, rg_metrics=rg, enforce_flag=False,
    )
    item = next(c for c in checklist if c.key == "zero_false_blocks")
    assert item.status == "fail"
    phase, state, blockers = _derive_phase_and_state(
        checklist=checklist, enforce_flag=False,
    )
    assert state == "Blocked"
    assert "zero_false_blocks" in blockers


def test_enforce_off_status_shown_correctly():
    dec, rg = _zero_metrics()
    # Enforce currently OFF — checklist shows enforce_off=pass.
    cl_off = _build_checklist(
        lane="equity",
        expected_collection="roadguard_equity_decisions",
        rg_collection="roadguard_equity_decisions",
        decision_metrics=dec, rg_metrics=rg, enforce_flag=False,
    )
    assert next(c for c in cl_off if c.key == "enforce_off").status == "pass"

    # Operator turned enforce ON before promotion proven — checklist
    # reflects this as a FAIL on enforce_off (the safety invariant).
    cl_on = _build_checklist(
        lane="equity",
        expected_collection="roadguard_equity_decisions",
        rg_collection="roadguard_equity_decisions",
        decision_metrics=dec, rg_metrics=rg, enforce_flag=True,
    )
    assert next(c for c in cl_on if c.key == "enforce_off").status == "fail"


def test_phase_enforce_when_flag_on_and_clean():
    dec = {"total": 500, "approved": 50, "no_trade": 450, "by_stage": {}, "false_blocks": 0}
    rg = {"total": 250, "by_decision": {"PASS": 245, "REDUCE": 4, "BLOCK": 1},
          "lane_mismatch": 0, "broker_health_blocks": 0,
          "exposure_cap_blocks": 1, "duplicate_symbol_blocks": 0,
          "cross_contamination": 0}
    checklist = _build_checklist(
        lane="equity",
        expected_collection="roadguard_equity_decisions",
        rg_collection="roadguard_equity_decisions",
        decision_metrics=dec, rg_metrics=rg, enforce_flag=True,
    )
    # enforce_off check FAILS when flag is on.
    assert next(c for c in checklist if c.key == "enforce_off").status == "fail"
    phase, state, blockers = _derive_phase_and_state(
        checklist=checklist, enforce_flag=True,
    )
    # Phase = Enforce regardless; state surfaces the regression.
    assert phase == "Enforce"
    assert state == "Blocked"
    assert "enforce_off" in blockers


def test_correct_collection_check_fails_on_mismatch():
    dec, rg = _zero_metrics()
    checklist = _build_checklist(
        lane="equity",
        expected_collection="roadguard_equity_decisions",
        rg_collection="roadguard_crypto_decisions",   # ← wrong!
        decision_metrics=dec, rg_metrics=rg, enforce_flag=False,
    )
    item = next(c for c in checklist if c.key == "correct_collection")
    assert item.status == "fail"


def test_broker_health_ratio_threshold():
    dec = {"total": 500, "approved": 0, "no_trade": 500, "by_stage": {}, "false_blocks": 0}
    # 10% broker health blocks — above default 5% threshold.
    rg = {"total": 100, "by_decision": {"PASS": 80, "REDUCE": 0, "BLOCK": 20},
          "lane_mismatch": 0, "broker_health_blocks": 10,
          "exposure_cap_blocks": 10, "duplicate_symbol_blocks": 0,
          "cross_contamination": 0}
    checklist = _build_checklist(
        lane="equity",
        expected_collection="roadguard_equity_decisions",
        rg_collection="roadguard_equity_decisions",
        decision_metrics=dec, rg_metrics=rg, enforce_flag=False,
    )
    item = next(c for c in checklist if c.key == "broker_health_stable")
    assert item.status == "fail"


def test_enforce_flag_for_reads_env():
    os.environ["ROADGUARD_EQUITY_ENFORCE_ENABLED"] = "true"
    try:
        assert _enforce_flag_for("equity") is True
    finally:
        del os.environ["ROADGUARD_EQUITY_ENFORCE_ENABLED"]
    assert _enforce_flag_for("equity") is False
    assert _enforce_flag_for("crypto") is False


# ── Endpoint integrity (no mutation) ─────────────────────────────


@pytest.mark.asyncio
async def test_get_kanban_does_not_mutate_db():
    """``get_kanban`` must perform NO writes. We assert this by
    passing in a stub DB that raises on any write attempt."""

    class _EmptyCursor:
        def __aiter__(self):
            return self
        async def __anext__(self):
            raise StopAsyncIteration

    class _ReadOnlyStubColl:
        def aggregate(self, *_a, **_kw):
            return _EmptyCursor()

        def find(self, *_a, **_kw):
            return _EmptyCursor()

        async def count_documents(self, *_a, **_kw):
            return 0

        async def find_one(self, *_a, **_kw):
            return None

        # Trip on any write.
        async def insert_one(self, *_a, **_kw):
            raise AssertionError("kanban must not write — insert_one called")

        async def update_one(self, *_a, **_kw):
            raise AssertionError("kanban must not write — update_one called")

        async def delete_one(self, *_a, **_kw):
            raise AssertionError("kanban must not write — delete_one called")

        async def delete_many(self, *_a, **_kw):
            raise AssertionError("kanban must not write — delete_many called")

    class _StubDB:
        def __getitem__(self, _name):
            return _ReadOnlyStubColl()

    out = await get_kanban(_StubDB())
    assert "lanes" in out
    assert set(out["lanes"].keys()) == {"equity", "crypto"}
    for lane in ("equity", "crypto"):
        card = out["lanes"][lane]
        assert card["lane"] == lane
        assert card["phase"] in ("Shadow", "Calibrate", "Enforce")
        assert card["promotion_state"] in ("Eligible", "Blocked", "Ready for Review")
        assert card["enforce_flag"] is False
        # Empty lane -> Shadow / Blocked
        assert card["phase"] == "Shadow"
        assert card["promotion_state"] == "Blocked"


@pytest.mark.asyncio
async def test_get_kanban_lanes_independent():
    """Equity and crypto cards must compute INDEPENDENTLY — a write
    to one lane's collection must not affect the other lane's metrics.
    """
    seeded_counts = {
        "alpha_decision_log": {"equity": 250, "crypto": 0},
        "roadguard_equity_decisions": 100,
        "roadguard_crypto_decisions": 0,
    }

    class _Cursor:
        def __init__(self, rows):
            self._rows = list(rows)
        def __aiter__(self):
            return self
        async def __anext__(self):
            if not self._rows:
                raise StopAsyncIteration
            return self._rows.pop(0)

    class _Coll:
        def __init__(self, name):
            self.name = name
        def aggregate(self, pipeline):
            if self.name == "alpha_decision_log":
                lane = pipeline[0]["$match"]["lane"]
                n = seeded_counts["alpha_decision_log"].get(lane, 0)
                if n == 0:
                    return _Cursor([])
                return _Cursor([{"_id": {"decision": "NO_TRADE",
                                          "blocked_at": "perception"}, "n": n}])
            n = seeded_counts.get(self.name, 0)
            if n == 0:
                return _Cursor([])
            lane_for_coll = (
                "equity" if self.name == "roadguard_equity_decisions" else "crypto"
            )
            return _Cursor([{
                "_id": {"decision": "PASS", "gate": None,
                         "verdict_lane": lane_for_coll},
                "n": n,
            }])
        async def count_documents(self, *_a, **_kw):
            return 0
        async def find_one(self, *_a, **_kw):
            return None

    class _DB:
        def __getitem__(self, name):
            return _Coll(name)

    out = await get_kanban(_DB())
    eq = out["lanes"]["equity"]
    cr = out["lanes"]["crypto"]
    assert eq["metrics"]["receipts"]["total"] == 250
    assert cr["metrics"]["receipts"]["total"] == 0
    assert eq["metrics"]["roadguard"]["total"] == 100
    assert cr["metrics"]["roadguard"]["total"] == 0
    assert cr["promotion_state"] == "Blocked"
    eq_keys = {c["key"]: c for c in eq["checklist"]}
    assert eq_keys["min_receipts"]["status"] == "pass"
    assert eq_keys["broker_health_stable"]["status"] == "pass"
