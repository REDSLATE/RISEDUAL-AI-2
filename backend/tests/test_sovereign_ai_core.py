"""
Tests for Sovereign AI Core + Promotion Gate.

Covers:
* Pure model contracts (strategist / regime / options / catalyst / risk / calibration)
* Coordinator veto supremacy + shadow fail-safe
* Feature snapshot round-trip (no BSON leakage)
* Promotion gate math (shadow / promote / demote)
* Resolution back-patching (idempotent)
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from services import sovereign_ai_core as sac
from services.sovereign_ai_core import (
    SovereignDecision,
    SovereignFeatures,
    resolve_sovereign_decision,
    sovereign_decide,
    _catalyst_model,
    _calibration_model,
    _classify_tier,
    _options_intent_model,
    _regime_model,
    _risk_model,
    _strategist_model,
)
from services import sovereign_promotion_gate as spg


# ─── Fake async Mongo — minimal stand-in ──────────────────────────────────────


class _FakeColl:
    def __init__(self, docs: list[dict[str, Any]] | None = None):
        self.docs = docs or []
        self.inserts: list[dict[str, Any]] = []

    async def insert_one(self, doc: dict[str, Any]):
        # simulate Mongo mutating the input with _id
        self.inserts.append(doc)
        self.docs.append(doc)
        doc["_id"] = f"objid-{len(self.inserts)}"
        return type("R", (), {"inserted_id": doc["_id"]})()

    async def find_one(self, query=None, projection=None, sort=None):  # noqa: ANN001
        query = query or {}
        matched = [d for d in self.docs if _matches(d, query)]
        if sort:
            key, order = sort[0] if isinstance(sort, list) else sort
            matched.sort(key=lambda d: d.get(key) or 0, reverse=(order == -1))
        return matched[0] if matched else None

    async def count_documents(self, query: dict[str, Any]) -> int:
        return sum(1 for d in self.docs if _matches(d, query))

    def find(self, query=None, projection=None):
        query = query or {}
        matched = [d for d in self.docs if _matches(d, query)]
        return _Cursor(matched)

    def aggregate(self, pipeline):
        # Minimal support: $match → $group(avg)
        match_stage = next((s.get("$match") for s in pipeline if "$match" in s), {}) or {}
        group_stage = next((s.get("$group") for s in pipeline if "$group" in s), None)
        matched = [d for d in self.docs if _matches(d, match_stage)]
        if group_stage and "avg" in group_stage and matched:
            # Rough avg on calibration_score
            avg = sum(d.get("calibration_score", 0) for d in matched) / max(1, len(matched))
            return _Cursor([{"_id": None, "avg": avg}])
        return _Cursor([])

    async def update_one(self, filt, update):
        for d in self.docs:
            if _matches(d, filt):
                for k, v in update.get("$set", {}).items():
                    _set_nested(d, k, v)
                return type("R", (), {"matched_count": 1, "modified_count": 1})()
        return type("R", (), {"matched_count": 0, "modified_count": 0})()

    async def create_index(self, *args, **kwargs):  # noqa: ANN002,ANN003
        return "idx"


class _Cursor:
    def __init__(self, docs):
        self.docs = docs

    def sort(self, *args, **kwargs):  # noqa: ANN002,ANN003
        return self

    def limit(self, n):
        self.docs = self.docs[:n]
        return self

    async def to_list(self, length=None):
        if length is None:
            return list(self.docs)
        return list(self.docs[:length])


class _FakeDB:
    def __init__(self):
        self._colls: dict[str, _FakeColl] = {}

    def __getitem__(self, name):
        return self._colls.setdefault(name, _FakeColl())

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return self[name]


def _matches(doc: dict[str, Any], query: dict[str, Any]) -> bool:
    for k, v in query.items():
        if isinstance(v, dict) and "$exists" in v:
            # nested key lookup
            got = _get_nested(doc, k)
            exists = got is not None
            if bool(v["$exists"]) != exists:
                return False
            continue
        if isinstance(v, dict) and "$gte" in v:
            got = _get_nested(doc, k)
            if got is None or got < v["$gte"]:
                return False
            continue
        if isinstance(v, dict) and "$lte" in v:
            got = _get_nested(doc, k)
            if got is None or got > v["$lte"]:
                return False
            continue
        got = _get_nested(doc, k)
        if got != v:
            return False
    return True


def _get_nested(doc: dict[str, Any], key: str):
    cur: Any = doc
    for part in key.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def _set_nested(doc: dict[str, Any], key: str, value: Any) -> None:
    parts = key.split(".")
    cur = doc
    for p in parts[:-1]:
        cur = cur.setdefault(p, {})
    cur[parts[-1]] = value


# ─── Pure sub-model tests ─────────────────────────────────────────────────────


def test_strategist_model_rsi_oversold_plus_positive_momentum_is_long():
    f = SovereignFeatures(symbol="X", asset_type="equity", rsi=30, momentum_5b=0.02)
    v = _strategist_model(f)
    assert v["action"] == "LONG"
    assert v["confidence"] > 0.5


def test_strategist_model_rsi_overbought_plus_negative_momentum_is_short():
    f = SovereignFeatures(symbol="X", asset_type="equity", rsi=70, momentum_5b=-0.02)
    v = _strategist_model(f)
    assert v["action"] == "SHORT"


def test_strategist_model_neutral_rsi_and_flat_momentum_is_hold():
    f = SovereignFeatures(symbol="X", asset_type="equity", rsi=50, momentum_5b=0.0)
    v = _strategist_model(f)
    assert v["action"] == "HOLD"


def test_regime_model_trend_up_full_gate():
    f = SovereignFeatures(symbol="X", asset_type="equity", regime="trend_up")
    assert _regime_model(f)["gate"] == 1.0


def test_regime_model_high_vol_halves_gate():
    f = SovereignFeatures(symbol="X", asset_type="equity", regime="high_vol")
    assert _regime_model(f)["gate"] == 0.5


def test_regime_model_unknown_defaults_to_conservative():
    f = SovereignFeatures(symbol="X", asset_type="equity", regime="")
    assert _regime_model(f)["gate"] == 0.3


def test_options_intent_no_data_passes_through():
    f = SovereignFeatures(symbol="X", asset_type="equity", options_flow_skew=None)
    v = _options_intent_model(f)
    assert v["gate"] == 1.0
    assert v.get("bullish_flow") is None or v["bullish_flow"] is False


def test_options_intent_bullish_flow_flagged():
    f = SovereignFeatures(symbol="X", asset_type="equity", options_flow_skew=0.4)
    v = _options_intent_model(f)
    assert v["bullish_flow"] is True
    assert v["bearish_flow"] is False


def test_catalyst_model_restricted_event_vetoes():
    f = SovereignFeatures(symbol="X", asset_type="equity", event_risk="restricted")
    v = _catalyst_model(f)
    assert "NEWS_SHOCK_RESTRICTED" in v["vetoes"]


def test_catalyst_model_high_shock_applies_negative_delta():
    f = SovereignFeatures(symbol="X", asset_type="equity", news_shock_state="high")
    v = _catalyst_model(f)
    assert v["delta"] == -0.10
    assert v["vetoes"] == []


def test_catalyst_model_normal_state_is_noop():
    f = SovereignFeatures(symbol="X", asset_type="equity", news_shock_state="normal")
    v = _catalyst_model(f)
    assert v["delta"] == 0.0
    assert v["vetoes"] == []


def test_risk_model_dollar_volume_starvation_triggers_liquidity_trap():
    f = SovereignFeatures(
        symbol="X", asset_type="equity",
        dollar_volume=100_000, dollar_volume_baseline=1_000_000,
    )
    v = _risk_model(f)
    assert "LIQUIDITY_TRAP" in v["vetoes"]


def test_risk_model_volatility_extreme_vetoes():
    f = SovereignFeatures(symbol="X", asset_type="equity", atr_pct=0.20)
    v = _risk_model(f)
    assert "VOLATILITY_EXTREME" in v["vetoes"]


def test_risk_model_clean_inputs_pass():
    f = SovereignFeatures(
        symbol="X", asset_type="equity",
        dollar_volume=1_000_000, dollar_volume_baseline=1_000_000,
        atr_pct=0.02, volume_zscore=0.5,
    )
    v = _risk_model(f)
    assert v["vetoes"] == []
    assert v["multiplier"] == 1.0


def test_calibration_model_rich_data_scores_higher():
    f = SovereignFeatures(
        symbol="X", asset_type="equity", rsi=45, momentum_5b=0.01,
        atr_pct=0.02, volume_zscore=0.3, regime="trend_up",
    )
    s = _strategist_model(f)
    assert _calibration_model(f, s)["score"] > 0.5


def test_classify_tier_boundaries():
    assert _classify_tier(0.90) == "extreme"
    assert _classify_tier(0.75) == "high"
    assert _classify_tier(0.60) == "medium"
    assert _classify_tier(0.40) == "low"


# ─── Coordinator tests ────────────────────────────────────────────────────────


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.fixture
def db():
    return _FakeDB()


def test_sovereign_decide_veto_forces_hold_and_zero_size(db):
    f = SovereignFeatures(
        symbol="AAPL", asset_type="equity", rsi=30, momentum_5b=0.02,
        event_risk="restricted",  # hard veto
    )
    d = _run(sovereign_decide(db, f, shadow=True, persist=False))
    assert d.action == "HOLD"
    assert d.size_multiplier == 0.0
    assert "NEWS_SHOCK_RESTRICTED" in d.vetoes


def test_sovereign_decide_clean_long_path(db):
    f = SovereignFeatures(
        symbol="AAPL", asset_type="equity", rsi=30, momentum_5b=0.03,
        regime="trend_up", atr_pct=0.02,
        dollar_volume=1_000_000, dollar_volume_baseline=1_000_000,
    )
    d = _run(sovereign_decide(db, f, shadow=True, persist=False))
    assert d.action == "LONG"
    assert d.vetoes == []
    assert d.size_multiplier > 0


def test_sovereign_decide_persists_without_bson_id(db):
    f = SovereignFeatures(symbol="AAPL", asset_type="equity", rsi=30, momentum_5b=0.02)
    d = _run(sovereign_decide(db, f, shadow=True, persist=True))
    inserts = db["sovereign_decisions"].inserts
    assert len(inserts) == 1
    # The doc Mongo returns via insert_one gets _id stamped; our helper must
    # NOT have relied on re-reading it (it doesn't — to_mongo_doc creates a
    # fresh dict from asdict). Decision object stays _id-free:
    assert not hasattr(d, "_id")
    assert d.feature_snapshot["symbol"] == "AAPL"


def test_sovereign_decide_internal_error_returns_safe_hold():
    class BustedFeatures:
        # Missing required attrs → triggers AttributeError deep in model
        symbol = "X"
        asset_type = "equity"

    d = _run(sovereign_decide(None, BustedFeatures(), shadow=True, persist=False))
    assert d.action == "HOLD"
    assert "SOVEREIGN_INTERNAL_ERROR" in d.vetoes
    assert d.shadow is True


def test_sovereign_decide_confidence_clamped_to_0_1(db):
    f = SovereignFeatures(
        symbol="X", asset_type="equity", rsi=10, momentum_5b=0.10,
        regime="trend_up",
    )
    d = _run(sovereign_decide(db, f, shadow=True, persist=False))
    assert 0.0 <= d.confidence <= 1.0
    assert 0.0 <= d.size_multiplier <= 1.0


def test_sovereign_decide_options_contra_reduces_conf(db):
    f_aligned = SovereignFeatures(
        symbol="X", asset_type="equity", rsi=30, momentum_5b=0.03,
        regime="trend_up", options_flow_skew=0.4,
    )
    f_contra = SovereignFeatures(
        symbol="X", asset_type="equity", rsi=30, momentum_5b=0.03,
        regime="trend_up", options_flow_skew=-0.4,
    )
    d_a = _run(sovereign_decide(db, f_aligned, shadow=True, persist=False))
    d_c = _run(sovereign_decide(db, f_contra, shadow=True, persist=False))
    assert d_a.confidence >= d_c.confidence


# ─── Resolution tests ─────────────────────────────────────────────────────────


def test_resolve_sovereign_decision_updates_outcome(db):
    f = SovereignFeatures(symbol="AAPL", asset_type="equity", rsi=30, momentum_5b=0.02)
    d = _run(sovereign_decide(db, f, shadow=True, persist=True))
    ok = _run(resolve_sovereign_decision(
        db, d.decision_id, horizon="60m", pnl_pct=0.015, was_right=True,
    ))
    assert ok is True
    stored = db["sovereign_decisions"].docs[0]
    assert stored["resolved"] is True
    assert stored["outcomes"]["60m"]["was_right"] is True


def test_resolve_sovereign_decision_idempotent_on_missing_id(db):
    ok = _run(resolve_sovereign_decision(
        db, "nonexistent", horizon="60m", pnl_pct=0.0, was_right=False,
    ))
    assert ok is False


def test_resolve_sovereign_decision_noop_on_null_db():
    ok = _run(resolve_sovereign_decision(
        None, "x", horizon="60m", pnl_pct=0.0, was_right=False,
    ))
    assert ok is False


# ─── Promotion gate tests ─────────────────────────────────────────────────────


def _seed_decisions(
    db: _FakeDB,
    *,
    asset_type: str,
    count: int,
    right_count: int,
    calibration: float = 0.7,
    horizon: str = "60m",
    recent_days: int = 1,
) -> None:
    coll = db["sovereign_decisions"]
    now = datetime.now(timezone.utc)
    for i in range(count):
        coll.docs.append({
            "decision_id": f"d-{asset_type}-{i}",
            "asset_type": asset_type,
            "shadow": True,
            "resolved": True,
            "outcomes": {
                horizon: {
                    "was_right": i < right_count,
                    "pnl_pct": 0.01,
                },
            },
            "calibration_score": calibration,
            "created_at": now - timedelta(days=min(i % recent_days + 1, 29)),
        })


def test_promotion_gate_below_threshold_is_shadow_only(db):
    _seed_decisions(db, asset_type="equity", count=100, right_count=90)
    st = _run(spg.compute_sovereign_promotion_status(db, "equity"))
    assert st["promoted"] is False
    assert st["phase"] == "phase_1_shadow_only"
    assert st["rows_resolved"] == 100
    assert st["rows_to_go"] > 0


def test_promotion_gate_promotes_when_all_conditions_clear(db, monkeypatch):
    # Tighten threshold so the test doesn't need to seed 500 rows
    monkeypatch.setattr(spg, "MIN_RESOLVED", 10)
    monkeypatch.setattr(spg, "MIN_WIN_RATE", 0.70)
    monkeypatch.setattr(spg, "MIN_CALIBRATION", 0.65)
    _seed_decisions(db, asset_type="equity", count=20, right_count=18, calibration=0.75)
    st = _run(spg.compute_sovereign_promotion_status(db, "equity"))
    assert st["promoted"] is True
    assert st["phase"] == "phase_2_authority"


def test_promotion_gate_demotes_on_rolling_win_rate_drop(db, monkeypatch):
    monkeypatch.setattr(spg, "MIN_RESOLVED", 10)
    monkeypatch.setattr(spg, "MIN_WIN_RATE", 0.70)
    monkeypatch.setattr(spg, "MIN_CALIBRATION", 0.65)
    monkeypatch.setattr(spg, "DEMOTE_BELOW", 0.55)
    # Seed 20 resolved rows but with a rolling 30d slice < 55%
    coll = db["sovereign_decisions"]
    now = datetime.now(timezone.utc)
    # Lifetime sample: 20 rows, 18 right (90% lifetime win rate)
    # Rolling 30d slice: 10 recent rows, only 4 right (40% — triggers demote)
    for i in range(10):
        coll.docs.append({
            "asset_type": "equity", "shadow": True, "resolved": True,
            "outcomes": {"60m": {"was_right": True, "pnl_pct": 0.01}},
            "calibration_score": 0.75,
            "created_at": now - timedelta(days=90),  # old, not in rolling
        })
    for i in range(10):
        coll.docs.append({
            "asset_type": "equity", "shadow": True, "resolved": True,
            "outcomes": {"60m": {"was_right": i < 4, "pnl_pct": 0.01}},
            "calibration_score": 0.75,
            "created_at": now - timedelta(days=5),  # recent
        })
    st = _run(spg.compute_sovereign_promotion_status(db, "equity"))
    assert st["demoted"] is True
    assert st["promoted"] is False


def test_is_sovereign_authority_default_false_on_fresh_db(db):
    result = _run(spg.is_sovereign_authority(db, "equity"))
    assert result is False


def test_promotion_gate_never_raises_on_mongo_error():
    class BrokenDB:
        def __getitem__(self, name):
            raise RuntimeError("mongo down")

    st = _run(spg.compute_sovereign_promotion_status(BrokenDB(), "equity"))
    assert st["phase"] == "phase_1_shadow_only"
    assert st["promoted"] is False
    assert "status probe failed" in (st["blocker"] or "")
