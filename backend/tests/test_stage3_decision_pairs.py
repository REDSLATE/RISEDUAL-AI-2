"""Stage 3 — Sovereign Voice + Filer + Outcome Writer test suite.

Covers:
* ``services.sovereign_voice.render_voice`` — deterministic templates
* ``services.intent_decision_filer.file_decision_pair`` — idempotent pair
  storage, agreement classification, ABSENT council fallback
* ``services.decision_outcome_writer`` — score_voice / decide_winner /
  write_outcome_for_trade / attach_council_verdict / aggregate_stats
* Endpoint module — owner-gate static check
"""
from __future__ import annotations

import asyncio
import inspect
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from services import decision_outcome_writer as dow
from services.intent_decision_filer import (
    COLLECTION as PAIRS_COLLECTION,
    _classify_agreement,
    _council_voice_from_hypothesis,
    fetch_recent_pairs,
    file_decision_pair,
)
from services.sovereign_voice import render_voice


def _run(coro):
    """Run a coroutine in a fresh event loop — avoids cross-test
    pollution when pytest-asyncio's loop is closed."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ── Tiny in-memory mongo stand-in ────────────────────────────────────


class _Coll:
    def __init__(self):
        self.docs: list[dict] = []

    async def insert_one(self, doc):
        self.docs.append(dict(doc))
        return type("R", (), {"inserted_id": "x"})()

    async def find_one(self, q=None, proj=None):
        for d in self.docs:
            if _match(d, q or {}):
                out = {k: v for k, v in d.items() if not (proj and k == "_id" and proj.get("_id") == 0)}
                return out
        return None

    def find(self, q=None, proj=None):
        matched = [d for d in self.docs if _match(d, q or {})]
        return _Cursor(matched, proj)

    async def update_one(self, q, upd):
        for d in self.docs:
            if _match(d, q):
                for k, v in upd.get("$set", {}).items():
                    _set_nested(d, k, v)
                return type("R", (), {"matched_count": 1, "modified_count": 1})()
        return type("R", (), {"matched_count": 0, "modified_count": 0})()

    async def count_documents(self, q):
        return sum(1 for d in self.docs if _match(d, q))


class _Cursor:
    def __init__(self, docs, proj=None):
        self.docs = docs
        self.proj = proj

    def sort(self, *a, **k):
        # Sort by created_at desc by default
        self.docs = sorted(
            self.docs, key=lambda d: d.get("created_at") or datetime.min, reverse=True,
        )
        return self

    def limit(self, n):
        self.docs = self.docs[: max(0, int(n))]
        return self

    def __aiter__(self):
        self._it = iter(self.docs)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration


class _DB:
    def __init__(self):
        self._cs: dict[str, _Coll] = {}

    def __getitem__(self, name):
        return self._cs.setdefault(name, _Coll())


def _set_nested(d, key, val):
    parts = key.split(".")
    cur = d
    for p in parts[:-1]:
        cur = cur.setdefault(p, {})
    cur[parts[-1]] = val


def _get_nested(d, key):
    cur = d
    for p in key.split("."):
        if isinstance(cur, dict) and p in cur:
            cur = cur[p]
        else:
            return None
    return cur


def _match(doc, q):
    for k, v in q.items():
        got = _get_nested(doc, k)
        if isinstance(v, dict):
            if "$gte" in v and (got is None or got < v["$gte"]):
                return False
            if "$lte" in v and (got is None or got > v["$lte"]):
                return False
            if "$in" in v and got not in v["$in"]:
                return False
            if "$type" in v and not isinstance(got, datetime):
                return False
            if "$lt" in v and (got is None or got >= v["$lt"]):
                return False
        else:
            if got != v:
                return False
    return True


# ── Helpers ──────────────────────────────────────────────────────────


def _sov_decision(*, action="LONG", conf=0.71, symbol="AAPL",
                  asset_type="equity", decision_id="dec-1",
                  trade_id=None) -> dict:
    """Build a representative SovereignDecision dict (Mongo doc shape)."""
    return {
        "decision_id": decision_id,
        "symbol": symbol,
        "asset_type": asset_type,
        "action": action,
        "direction": action,
        "confidence": conf,
        "conviction_tier": "high" if conf > 0.7 else "mid",
        "size_multiplier": 1.0,
        "reasons": ["rsi_oversold", "momentum_positive"],
        "vetoes": [],
        "model_votes": {
            "strategist": {"action": action, "confidence": conf},
            "regime": {"gate": 0.9, "regime": "trend_up"},
            "options_intent": {"skew": 0.3, "bullish_flow": action == "LONG"},
            "memory": {"similar_n": 15, "similar_win_rate": 0.62},
            "adversarial": {"bull_score": 0.7, "bear_score": 0.3},
            "calibration": {"score": 0.55},
        },
        "advisory_votes": {},
        "feature_snapshot": {
            "rsi": 32,
            "momentum_5b": 0.018,
            "regime": "trend_up",
            "dollar_volume": 1.0e9,
            "dollar_volume_baseline": 1.1e9,
            "news_shock_state": "normal",
            "event_risk": "normal",
        },
        "calibration_score": 0.55,
        "shadow": True,
        "created_at": datetime.now(timezone.utc),
        "resolved": False,
        "outcomes": {},
    }


# ── render_voice ─────────────────────────────────────────────────────


def test_voice_no_llm_imports():
    """sovereign_voice must remain pure — no openai/anthropic/google."""
    import services.sovereign_voice as sv  # noqa: WPS433
    src = inspect.getsource(sv)
    for forbidden in ("openai", "anthropic", "google.generativeai",
                      "emergentintegrations", "litellm"):
        assert forbidden not in src, f"sovereign_voice must not import {forbidden}"


def test_voice_renders_long_with_drivers_and_warnings():
    d = _sov_decision(action="LONG", conf=0.71)
    v = render_voice(d)
    assert v["action"] == "LONG"
    assert v["confidence"] == 0.71
    assert v["verdict"] == "LONG 0.71"
    # Drivers should include trend (strategist) since it agrees.
    driver_names = {r["submodel"] for r in v["key_drivers"]}
    assert "trend" in driver_names
    # Rationale text contains action + cited drivers
    assert "LONG 0.71" in v["rationale_text"]
    assert "Drivers:" in v["rationale_text"]


def test_voice_renders_warnings_when_news_shock_elevated():
    d = _sov_decision()
    d["feature_snapshot"]["news_shock_state"] = "elevated"
    v = render_voice(d)
    assert any("news_shock=elevated" in w for w in v["warnings"])


def test_voice_renders_small_sample_memory_warning():
    d = _sov_decision()
    d["model_votes"]["memory"]["similar_n"] = 3
    v = render_voice(d)
    assert any("small_sample_memory" in w for w in v["warnings"])


def test_voice_renders_hold_with_no_drivers():
    d = _sov_decision(action="HOLD", conf=0.40)
    v = render_voice(d)
    assert v["action"] == "HOLD"
    # HOLD action — no sub-model "agrees" by default (only LONG/SHORT do)
    # but the structure stays stable.
    assert "key_drivers" in v
    assert "dissent_flags" in v
    assert "rationale_text" in v


def test_voice_payload_shape_is_stable():
    d = _sov_decision()
    v = render_voice(d)
    for key in (
        "verdict", "action", "confidence", "tier",
        "key_drivers", "dissent_flags", "warnings", "rationale_text",
    ):
        assert key in v


# ── intent_decision_filer ────────────────────────────────────────────


def test_classify_agreement_table():
    assert _classify_agreement("LONG", "LONG") == "AGREE"
    assert _classify_agreement("LONG", "SHORT") == "DISAGREE"
    assert _classify_agreement("LONG", "HOLD") == "PARTIAL"
    assert _classify_agreement("HOLD", "LONG") == "PARTIAL"


def test_council_voice_from_hypothesis_handles_absent():
    voice = _council_voice_from_hypothesis(None)
    assert voice["action"] == "UNKNOWN"
    assert voice["verdict"] == "ABSENT"


def test_council_voice_normalizes_confidence_units():
    # Confidence > 1 → percentage form → must be /100
    voice = _council_voice_from_hypothesis(
        {"display_action": "LONG", "final_confidence": 72, "summary": "Trend strong"},
    )
    assert voice["action"] == "LONG"
    assert 0.71 <= voice["confidence"] <= 0.73
    # Unit-scale confidence stays unit-scale
    voice2 = _council_voice_from_hypothesis(
        {"display_action": "SHORT", "confidence": 0.55, "summary": ""},
    )
    assert 0.54 <= voice2["confidence"] <= 0.56


def test_file_decision_pair_inserts_and_returns_doc():
    async def go():
        db = _DB()
        sov = _sov_decision(decision_id="d1")
        out = await file_decision_pair(
            db, sovereign_decision=sov, council_hypothesis=None,
            trade_id="t1", trace_id="abc12345",
        )
        assert out["decision_id"] == "d1"
        assert out["trade_id"] == "t1"
        assert out["sovereign"]["action"] == "LONG"
        assert out["council"]["action"] == "UNKNOWN"
        assert out["resolved"] is False
        assert out["agreement"] in ("AGREE", "PARTIAL", "DISAGREE")
        assert "_id" not in out
        # one row in collection
        assert len(db[PAIRS_COLLECTION].docs) == 1
    _run(go())


def test_file_decision_pair_is_idempotent_on_decision_id():
    async def go():
        db = _DB()
        sov = _sov_decision(decision_id="d-dup")
        await file_decision_pair(db, sovereign_decision=sov, council_hypothesis=None)
        # Second call with same decision_id should be a no-op
        await file_decision_pair(db, sovereign_decision=sov, council_hypothesis=None)
        assert len(db[PAIRS_COLLECTION].docs) == 1
    _run(go())


def test_file_decision_pair_requires_decision_id():
    async def go():
        db = _DB()
        sov = _sov_decision()
        sov.pop("decision_id", None)
        with pytest.raises(ValueError):
            await file_decision_pair(
                db, sovereign_decision=sov, council_hypothesis=None,
            )
    _run(go())


def test_fetch_recent_pairs_filters_by_lane_and_agreement():
    async def go():
        db = _DB()
        # 3 pairs: one equity AGREE, one crypto DISAGREE, one equity PARTIAL
        for dec_id, sym, asset, action, council_act in (
            ("d1", "AAPL", "equity", "LONG", "LONG"),
            ("d2", "BTCUSD", "crypto", "LONG", "SHORT"),
            ("d3", "TSLA", "equity", "LONG", "HOLD"),
        ):
            council_hyp = (
                None if council_act == "UNKNOWN"
                else {"display_action": council_act, "final_confidence": 60,
                      "summary": ""}
            )
            await file_decision_pair(
                db,
                sovereign_decision=_sov_decision(
                    action=action, symbol=sym, asset_type=asset, decision_id=dec_id,
                ),
                council_hypothesis=council_hyp,
            )
        eq = await fetch_recent_pairs(db, lane="equity")
        assert all(r["lane"] == "equity" for r in eq)
        agree = await fetch_recent_pairs(db, agreement="AGREE")
        assert len(agree) == 1 and agree[0]["decision_id"] == "d1"
        disagree = await fetch_recent_pairs(db, agreement="DISAGREE")
        assert len(disagree) == 1 and disagree[0]["decision_id"] == "d2"
    _run(go())


# ── decision_outcome_writer ──────────────────────────────────────────


def test_score_voice_long_win_is_correct():
    assert dow._score_voice("LONG", "LONG", "win") is True
    assert dow._score_voice("LONG", "LONG", "loss") is False


def test_score_voice_short_win_is_correct():
    assert dow._score_voice("SHORT", "SHORT", "win") is True


def test_score_voice_opposite_action_loss_is_correct():
    # LONG voice on a SHORT trade that lost → voice was right to argue
    # the opposite side
    assert dow._score_voice("LONG", "SHORT", "loss") is True
    assert dow._score_voice("SHORT", "LONG", "loss") is True


def test_score_voice_hold_on_loss_is_correct():
    assert dow._score_voice("HOLD", "LONG", "loss") is True
    assert dow._score_voice("HOLD", "LONG", "win") is False
    assert dow._score_voice("HOLD", "LONG", "flat") is True


def test_decide_winner_one_sided_when_council_absent():
    assert dow._decide_winner(True, False, "UNKNOWN") == "sovereign"
    assert dow._decide_winner(False, False, "UNKNOWN") == "neither"


def test_decide_winner_tie_and_disagreement():
    assert dow._decide_winner(True, True, "LONG") == "tie"
    assert dow._decide_winner(True, False, "LONG") == "sovereign"
    assert dow._decide_winner(False, True, "LONG") == "council"
    assert dow._decide_winner(False, False, "LONG") == "neither"


def test_write_outcome_stamps_pair_and_scoreboard():
    async def go():
        db = _DB()
        sov = _sov_decision(decision_id="d-out", action="LONG")
        await file_decision_pair(
            db, sovereign_decision=sov, council_hypothesis={
                "display_action": "LONG", "final_confidence": 65, "summary": "",
            }, trade_id="trade-XYZ",
        )
        res = await dow.write_outcome_for_trade(
            db,
            trade_id="trade-XYZ", direction="LONG",
            pnl_usd=120.5, pnl_pct=0.012, outcome_label="win",
        )
        assert res["ok"] is True
        assert res["winner"] == "tie"
        assert res["sovereign_correct"] is True
        assert res["council_correct"] is True
        row = await db[PAIRS_COLLECTION].find_one(
            {"decision_id": "d-out"}, {"_id": 0},
        )
        assert row["resolved"] is True
        assert row["outcome"]["pnl_usd"] == 120.5
        assert row["outcome"]["scoreboard"]["winner"] == "tie"
    _run(go())


def test_write_outcome_returns_not_found_when_no_pair():
    async def go():
        db = _DB()
        res = await dow.write_outcome_for_trade(
            db, trade_id="missing", direction="LONG",
            pnl_usd=0, pnl_pct=0, outcome_label="flat",
        )
        assert res["ok"] is False
        assert res["reason"] == "pair_not_found"
    _run(go())


def test_attach_council_verdict_updates_open_pair():
    async def go():
        db = _DB()
        sov = _sov_decision(decision_id="d-attach", symbol="NVDA",
                            action="LONG")
        await file_decision_pair(
            db, sovereign_decision=sov, council_hypothesis=None,
        )
        out = await dow.attach_council_verdict(
            db, symbol="NVDA",
            council_hypothesis={
                "display_action": "LONG", "final_confidence": 60, "summary": "",
            },
        )
        assert out["ok"] is True
        assert out["agreement"] == "AGREE"
        row = await db[PAIRS_COLLECTION].find_one(
            {"decision_id": "d-attach"}, {"_id": 0},
        )
        assert row["council"]["action"] == "LONG"
        assert row["agreement"] == "AGREE"
    _run(go())


def test_attach_council_skips_when_too_old():
    async def go():
        db = _DB()
        # Insert a pair directly with old created_at (simulating a stale pair)
        old_pair = {
            "decision_id": "d-old",
            "symbol": "OLD",
            "asset_type": "equity",
            "lane": "equity",
            "created_at": datetime.now(timezone.utc) - timedelta(hours=2),
            "trace_id": None,
            "trade_id": None,
            "sovereign": {"action": "LONG", "confidence": 0.7,
                          "verdict": "LONG 0.70", "rationale_text": ""},
            "council": {"action": "UNKNOWN", "verdict": "ABSENT",
                        "confidence": 0.0, "rationale_text": ""},
            "agreement": "PARTIAL",
            "resolved": False,
            "outcome": None,
        }
        await db[PAIRS_COLLECTION].insert_one(old_pair)
        out = await dow.attach_council_verdict(
            db, symbol="OLD",
            council_hypothesis={"display_action": "LONG", "final_confidence": 60},
            window_seconds=600,  # 10 min window
        )
        assert out["ok"] is False
        assert out["reason"] == "no_pair_in_window"
    _run(go())


def test_aggregate_stats_shapes_correctly():
    async def go():
        db = _DB()
        # 3 pairs: 2 resolved (1 sov-win tie, 1 council-only-win), 1 unresolved
        await file_decision_pair(
            db, sovereign_decision=_sov_decision(decision_id="d-a", action="LONG"),
            council_hypothesis={"display_action": "LONG", "final_confidence": 60},
            trade_id="t-a",
        )
        await dow.write_outcome_for_trade(
            db, trade_id="t-a", direction="LONG",
            pnl_usd=10, pnl_pct=0.01, outcome_label="win",
        )

        await file_decision_pair(
            db,
            sovereign_decision=_sov_decision(decision_id="d-b", action="SHORT"),
            council_hypothesis={"display_action": "LONG", "final_confidence": 60},
            trade_id="t-b",
        )
        await dow.write_outcome_for_trade(
            db, trade_id="t-b", direction="LONG",
            pnl_usd=10, pnl_pct=0.01, outcome_label="win",
        )

        # unresolved
        await file_decision_pair(
            db, sovereign_decision=_sov_decision(decision_id="d-c"),
            council_hypothesis=None,
        )

        stats = await dow.aggregate_stats(db)
        assert stats["total_pairs"] == 3
        assert stats["resolved"] == 2
        assert stats["unresolved"] == 1
        # d-a: sov LONG + council LONG + LONG win → tie
        # d-b: sov SHORT + council LONG + LONG win → council
        assert stats["winners"]["tie"] == 1
        assert stats["winners"]["council"] == 1
        assert stats["sovereign"]["correct"] == 1
        assert stats["council"]["correct"] == 2
    _run(go())


# ── Endpoint module — static checks ──────────────────────────────────


def test_admin_decision_pairs_endpoints_require_owner():
    """Both endpoints must call _require_owner."""
    import routes.admin_decision_pairs as ep
    src = inspect.getsource(ep)
    # Every endpoint coroutine must call _require_owner
    assert src.count("await _require_owner(request)") >= 2
    assert "Owner only" in src


def test_admin_decision_pairs_router_prefix():
    from routes.admin_decision_pairs import router
    assert router.prefix == "/api/admin/decision-pairs"
