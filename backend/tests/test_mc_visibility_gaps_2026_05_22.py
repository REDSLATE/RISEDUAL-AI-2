"""MC Visibility Gap tests (2026-05-22) — Gap 1 + Gap 2.

Gap 1: ``reconcile_alpaca_orders --enqueue-outcomes`` must FIFO
       pair mirrored Alpaca BUY/SELL fills into resolved outcomes
       and push them onto the Sovereign outcome inbox, threading
       the BUY lot's provenance (sovereign_decision_id /
       prediction_id / source_signal) through to MC.

Gap 2: ``LocalState.add_outcome`` accepts the new provenance
       fields, ``RECENT_OUTCOME_FIELDS`` lists them, and
       ``mc_client.build_contribution_body`` emits them on the
       wire when present.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest


# ─────────────────────────────────────────────────────────────────
# Gap 1 — Outcome pairer + reconciler --enqueue-outcomes wiring
# ─────────────────────────────────────────────────────────────────

from services.backfill_outcome_pairer import (
    _outcome_label,
    pair_fills_into_outcomes,
)


def test_outcome_label_threshold_table():
    assert _outcome_label(0.01) == "win"
    assert _outcome_label(-0.01) == "loss"
    assert _outcome_label(0.0) == "flat"
    assert _outcome_label(0.003) == "flat"  # below win threshold


def test_pair_fifo_round_trip_wins_loses_threading_provenance():
    """One BUY + one SELL on the same symbol → one resolved
    outcome carrying the BUY lot's provenance fields."""
    rows = [
        {
            "trade_id": "buy-1",
            "symbol": "AAPL",
            "side": "buy",
            "qty": 10.0,
            "entry_price": 100.0,
            "opened_at": datetime(2026, 5, 10, 14, 30, tzinfo=timezone.utc),
            "confidence": 0.72,
            "sovereign_decision_id": "sov-7",
            "prediction_id": None,
            "source_signal": "sovereign_decision",
        },
        {
            "trade_id": "sell-1",
            "symbol": "AAPL",
            "side": "sell",
            "qty": 10.0,
            "entry_price": 105.0,  # exit price stored in entry_price by reconciler
            "opened_at": datetime(2026, 5, 10, 15, 0, tzinfo=timezone.utc),
            "confidence": 0.0,
            "sovereign_decision_id": None,
            "prediction_id": None,
            "source_signal": None,
        },
    ]
    outs = pair_fills_into_outcomes(rows)
    assert len(outs) == 1
    o = outs[0]
    assert o["symbol"] == "AAPL"
    assert o["outcome_label"] == "win"  # +5%
    assert o["confidence"] == pytest.approx(0.72)
    assert o["sovereign_decision_id"] == "sov-7"
    assert o["source_signal"] == "sovereign_decision"
    assert o["buy_trade_id"] == "buy-1"
    assert o["sell_trade_id"] == "sell-1"
    assert o["notional"] == pytest.approx(1000.0)  # qty * entry


def test_pair_fifo_partial_close_two_lots():
    """Two BUY lots, one SELL covering both → two resolved
    outcomes drawn FIFO from the oldest lot first."""
    rows = [
        {
            "trade_id": "buy-1", "symbol": "NVDA", "side": "buy",
            "qty": 5.0, "entry_price": 100.0,
            "opened_at": "2026-05-10T14:00:00+00:00",
            "confidence": 0.6, "source_signal": "prediction",
            "prediction_id": "p-1",
        },
        {
            "trade_id": "buy-2", "symbol": "NVDA", "side": "buy",
            "qty": 5.0, "entry_price": 110.0,
            "opened_at": "2026-05-10T14:30:00+00:00",
            "confidence": 0.7, "source_signal": "sovereign_decision",
            "sovereign_decision_id": "sov-9",
        },
        {
            "trade_id": "sell-1", "symbol": "NVDA", "side": "sell",
            "qty": 10.0, "entry_price": 120.0,
            "opened_at": "2026-05-10T15:00:00+00:00",
        },
    ]
    outs = pair_fills_into_outcomes(rows)
    assert len(outs) == 2
    # First out drains buy-1 (oldest) at +20%
    assert outs[0]["buy_trade_id"] == "buy-1"
    assert outs[0]["outcome_label"] == "win"
    assert outs[0]["prediction_id"] == "p-1"
    assert outs[0]["source_signal"] == "prediction"
    # Second out drains buy-2 at +9%
    assert outs[1]["buy_trade_id"] == "buy-2"
    assert outs[1]["sovereign_decision_id"] == "sov-9"
    assert outs[1]["outcome_label"] == "win"


def test_pair_fifo_skips_unmatched_sell():
    """A SELL with no preceding BUY in the window is silently
    skipped — the inbox is for resolved trades only."""
    rows = [
        {
            "trade_id": "sell-orphan", "symbol": "X", "side": "sell",
            "qty": 1.0, "entry_price": 50.0, "opened_at": "t",
        },
    ]
    assert pair_fills_into_outcomes(rows) == []


def test_pair_fifo_loss_label():
    rows = [
        {"trade_id": "b", "symbol": "T", "side": "buy", "qty": 1,
         "entry_price": 100, "opened_at": "1"},
        {"trade_id": "s", "symbol": "T", "side": "sell", "qty": 1,
         "entry_price": 95, "opened_at": "2"},
    ]
    outs = pair_fills_into_outcomes(rows)
    assert outs[0]["outcome_label"] == "loss"


def test_reconcile_script_advertises_enqueue_outcomes_flag():
    """Static authority: the script's argparse must list the
    ``--enqueue-outcomes`` flag and the reconcile function must
    accept it as a kwarg."""
    src = Path("/app/backend/scripts/reconcile_alpaca_orders.py").read_text(
        encoding="utf-8",
    )
    assert "--enqueue-outcomes" in src
    assert "enqueue_outcomes: bool" in src
    # Pairer + bridge wired inside the reconcile function.
    assert "pair_fills_into_outcomes" in src
    assert "from services.sovereign_outcome_bridge import enqueue_outcome" in src


# ─────────────────────────────────────────────────────────────────
# Gap 2 — LocalState + mc_client provenance threading
# ─────────────────────────────────────────────────────────────────

from sovereign.local_state import LocalState, RECENT_OUTCOME_FIELDS
from sovereign.mc_client import build_contribution_body


def test_recent_outcome_fields_lists_provenance():
    """The canonical schema list MUST include the new provenance
    fields so MC's audit lineage stays buildable."""
    for f in ("sovereign_decision_id", "prediction_id", "source_signal"):
        assert f in RECENT_OUTCOME_FIELDS


def test_local_state_add_outcome_accepts_and_stores_provenance(tmp_path):
    s = LocalState(brain="alpha", path=str(tmp_path / "state.json"), mode="DTD")
    s.add_outcome(
        symbol="AAPL", action="BUY", confidence=0.7, outcome=1,
        notional=500.0,
        sovereign_decision_id="sov-42",
        prediction_id="pred-9",
        source_signal="sovereign_decision",
    )
    outs = s.recent_outcomes()
    assert len(outs) == 1
    o = outs[0]
    assert o["sovereign_decision_id"] == "sov-42"
    assert o["prediction_id"] == "pred-9"
    assert o["source_signal"] == "sovereign_decision"


def test_local_state_add_outcome_omits_missing_provenance(tmp_path):
    """Older callers that don't pass provenance must still work
    AND the record must NOT contain null provenance keys (MC
    schema is additive — emitting nulls would pollute it)."""
    s = LocalState(brain="alpha", path=str(tmp_path / "s.json"), mode="DTD")
    s.add_outcome(
        symbol="X", action="BUY", confidence=0.5, outcome=0,
    )
    o = s.recent_outcomes()[0]
    for f in ("sovereign_decision_id", "prediction_id", "source_signal"):
        assert f not in o


def test_local_state_persists_provenance_across_save_load(tmp_path):
    path = tmp_path / "state.json"
    s1 = LocalState(brain="alpha", path=str(path), mode="DTD")
    s1.set_weights({"trend": 0.5})
    s1.add_outcome(
        symbol="MSFT", action="SELL", confidence=0.6, outcome=-1,
        sovereign_decision_id="sov-1", source_signal="sovereign_decision",
    )
    s1.save()
    s2 = LocalState(brain="alpha", path=str(path), mode="DTD")
    o = s2.recent_outcomes()[0]
    assert o["sovereign_decision_id"] == "sov-1"
    assert o["source_signal"] == "sovereign_decision"


def test_build_contribution_body_emits_provenance_on_wire():
    """``build_contribution_body`` must forward provenance fields
    into ``validated_outs`` so MC sees them. Missing provenance
    yields no key (not a null) — schema stays additive."""
    body = build_contribution_body(
        mode="DTD",
        weights={"trend": 0.1},
        learning_rate=0.05,
        recent_outcomes=[
            {
                "symbol": "AAPL", "action": "BUY", "confidence": 0.7,
                "outcome": 1, "resolved_at": "2026-05-22",
                "notional": 100.0,
                "sovereign_decision_id": "sov-42",
                "source_signal": "sovereign_decision",
            },
            {
                "symbol": "X", "action": "SELL", "confidence": 0.5,
                "outcome": 0, "resolved_at": "2026-05-22",
                "notional": 50.0,
            },
        ],
    )
    outs = body["recent_outcomes"]
    assert outs[0]["sovereign_decision_id"] == "sov-42"
    assert outs[0]["source_signal"] == "sovereign_decision"
    # Missing values omitted — never emitted as null.
    assert "sovereign_decision_id" not in outs[1]
    assert "prediction_id" not in outs[1]
    assert "source_signal" not in outs[1]


# ─────────────────────────────────────────────────────────────────
# Bridge + closer threading (the writer half of Gap 2)
# ─────────────────────────────────────────────────────────────────


class _Coll:
    def __init__(self):
        self.docs: list[dict] = []

    async def find_one(self, q=None, proj=None):
        for d in self.docs:
            if all(d.get(k) == v for k, v in (q or {}).items()):
                return dict(d)
        return None

    async def insert_one(self, doc):
        d = dict(doc)
        d.setdefault("_id", f"id-{len(self.docs)+1}")
        self.docs.append(d)
        return type("R", (), {"inserted_id": d["_id"]})()


class _DB:
    def __init__(self):
        self._cs: dict[str, _Coll] = {}

    def __getitem__(self, name):
        return self._cs.setdefault(name, _Coll())


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_enqueue_outcome_stores_provenance_top_level():
    """The bridge must promote provenance from kwargs to top-
    level columns (NOT just bury them in ``extras``) so the
    sidecar's drainer + MC client can forward them without
    parsing nested objects."""
    from services.sovereign_outcome_bridge import enqueue_outcome, COLLECTION
    db = _DB()
    _run(enqueue_outcome(
        db, brain="alpha", trade_id="t-prov", symbol="NVDA",
        direction="up", confidence=0.65, outcome_label="win",
        sovereign_decision_id="sov-77",
        prediction_id=None,
        source_signal="sovereign_decision",
    ))
    r = db[COLLECTION].docs[0]
    assert r["sovereign_decision_id"] == "sov-77"
    assert r["source_signal"] == "sovereign_decision"
    # Empty prediction_id stored as None (not "None" string).
    assert r["prediction_id"] is None


def test_paper_trade_closer_forwards_provenance_static():
    """Static authority: the closer's enqueue call site must
    forward provenance fields from the trade record. Without
    this, paper trades resolved via the auto-closer arrive at
    MC stripped of their lineage."""
    src = Path("/app/backend/services/paper_trade_closer.py").read_text(
        encoding="utf-8",
    )
    assert "sovereign_decision_id=t.get(\"sovereign_decision_id\")" in src
    assert "prediction_id=t.get(\"prediction_id\")" in src
    assert "source_signal=t.get(\"source_signal\")" in src


def test_sidecar_forwards_provenance_to_add_outcome_static():
    """Static authority: the sidecar's drain → add_outcome
    handoff must pass provenance through so MC's recent_outcomes
    snapshot carries the audit lineage."""
    src = Path("/app/backend/sovereign/sidecar.py").read_text(encoding="utf-8")
    assert "sovereign_decision_id=(" in src
    assert "prediction_id=(" in src
    assert "source_signal=(" in src
