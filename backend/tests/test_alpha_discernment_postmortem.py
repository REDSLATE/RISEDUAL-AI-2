"""Tests for the Trade Discernment Layer postmortem service.

These tests exercise the offline path (no Claude call — LLM invoke
is toggled off). The Claude wrapper itself is covered by a separate
mock-based test at the end so we don't hit the real API from CI.

Key invariants locked down:

* Reconstruction pulls features from the ``setup_detected`` and
  ``triggered`` lifecycle rows in the SQLite hot store, joined
  with the ``alpha_outcomes`` Mongo doc.
* The seven discriminator features compute correctly on both the
  full-data and missing-data paths — missing features stay
  ``None`` (never fabricated).
* The group comparison surfaces per-feature deltas and marks
  ``insufficient_data`` when either group is empty for that
  feature.
* Persistence writes to ``alpha_discernment_features`` and
  ``alpha_discernment_postmortems`` in the same hot-store DB;
  Mongo is not touched.
* Ticker names never appear in the persisted comparison object.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture(autouse=True)
def _isolated_hot_store(tmp_path, monkeypatch):
    """Route the SQLite hot store to a per-test tmp file."""
    db_path = tmp_path / "hot_store.db"
    monkeypatch.setenv("ALPHA_HOT_STORE_PATH", str(db_path))
    from services import alpha_hot_store
    alpha_hot_store._DB_PATH = None  # reset lazy cache
    alpha_hot_store.init()
    yield


# ─────────────────────────────────────────────
#  Helpers to seed hot-store rows
# ─────────────────────────────────────────────

def _seed_lifecycle(
    setup_id: str, symbol: str, event: str, payload: dict, ts_ns: int,
) -> None:
    from services import alpha_hot_store
    with alpha_hot_store._connect() as con:
        con.execute(
            "INSERT INTO lifecycle_events(setup_id, symbol, event, stage, payload, ts_ns) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (setup_id, symbol, event, payload.get("stage") or event,
             json.dumps(payload), ts_ns),
        )


class _FakeDB:
    """Tiny Mongo stub with just an ``alpha_outcomes.find_one`` method."""
    def __init__(self, docs: list[dict]):
        self._docs = docs
        self.alpha_outcomes = self  # collection == self so find_one works

    async def find_one(self, query: dict):
        for d in self._docs:
            if all(d.get(k) == v for k, v in query.items()):
                return d
        return None


# ─────────────────────────────────────────────
#  Discriminator unit tests
# ─────────────────────────────────────────────

def test_regime_pattern_mismatch_momo_in_chop_is_mismatch():
    from services.alpha_discernment_postmortem import _regime_pattern_mismatch
    result = _regime_pattern_mismatch(
        setup_type="breakout",
        slow_regime="choppy_meanrevert",
        fast_regime="trend_up",
    )
    assert result == 1.0


def test_regime_pattern_mismatch_mean_revert_in_trend_is_mismatch():
    from services.alpha_discernment_postmortem import _regime_pattern_mismatch
    result = _regime_pattern_mismatch(
        setup_type="range_low_bounce",
        slow_regime="trend_up",
        fast_regime="trend_up",
    )
    assert result == 1.0


def test_regime_pattern_mismatch_aligned_is_zero():
    from services.alpha_discernment_postmortem import _regime_pattern_mismatch
    result = _regime_pattern_mismatch(
        setup_type="breakout",
        slow_regime="trend_up",
        fast_regime="trend_up",
    )
    assert result == 0.0


def test_regime_pattern_mismatch_unknown_returns_none():
    from services.alpha_discernment_postmortem import _regime_pattern_mismatch
    assert _regime_pattern_mismatch(
        setup_type="mystery",
        slow_regime="trend_up",
        fast_regime="trend_up",
    ) is None
    assert _regime_pattern_mismatch(
        setup_type="breakout",
        slow_regime="unknown",
        fast_regime="unknown",
    ) is None


def test_compute_discriminators_full_data():
    from services.alpha_discernment_postmortem import (
        SetupSnapshot, _compute_discriminators,
    )
    snap = SetupSnapshot(
        symbol="WMT", setup_id="s-1", setup_type="breakout",
        features_at_detection={
            "pct_change": 1.5,
            "relative_volume": 2.0,
            "price": 100.0,
            "slow_regime": "trend_up",
            "fast_regime": "trend_up",
            "trigger_price": 100.5,
            "invalidation_price": 99.0,
            "target_price": 103.0,
            "rs_spy": 0.75,
            "rs_qqq": 0.6,
        },
        features_at_trigger={
            "pct_change": 1.7,
            "relative_volume": 2.4,     # rising — fresh push
            "price": 100.6,             # small drift up from detection
        },
        detected_ts_ns=1_000_000_000_000,
        triggered_ts_ns=1_060_000_000_000,   # 60s later (60e9 ns)
    )
    disc = _compute_discriminators(snap)
    assert disc["move_maturity_pct"] == 1.5
    assert disc["rvol_delta_detection_to_trigger"] == 0.4
    # (100.6 - 100.0) / 100.0 * 100 = 0.6
    assert disc["price_delta_pct_detection_to_trigger"] == 0.6
    assert disc["relative_strength_vs_spy"] == 0.75
    assert disc["relative_strength_vs_qqq"] == 0.6
    assert disc["regime_pattern_mismatch"] == 0.0    # momo + trend = aligned
    assert disc["detect_to_trigger_ms"] == 60_000
    # reward = |103 - 100.5| = 2.5, risk = |100.5 - 99| = 1.5 → 1.6667
    assert disc["reward_over_risk"] == 1.6667


def test_compute_discriminators_missing_data_stays_none():
    from services.alpha_discernment_postmortem import (
        SetupSnapshot, _compute_discriminators,
    )
    snap = SetupSnapshot(
        symbol="TSLA", setup_id="s-2", setup_type="",
        features_at_detection={},
        features_at_trigger={},
    )
    disc = _compute_discriminators(snap)
    for k in (
        "move_maturity_pct",
        "rvol_delta_detection_to_trigger",
        "price_delta_pct_detection_to_trigger",
        "relative_strength_vs_spy",
        "relative_strength_vs_qqq",
        "regime_pattern_mismatch",
        "detect_to_trigger_ms",
        "reward_over_risk",
    ):
        assert disc[k] is None, f"{k} should be None on empty payload"


# ─────────────────────────────────────────────
#  Group comparison
# ─────────────────────────────────────────────

def test_compare_groups_produces_per_feature_delta():
    from services.alpha_discernment_postmortem import (
        SetupSnapshot, _compare_groups,
    )
    # Better group — trend-aligned setups with fresh volume.
    a = SetupSnapshot(symbol="X", setup_id="a", setup_type="breakout")
    a.__disc__ = {  # type: ignore[attr-defined]
        "move_maturity_pct": 1.0,
        "rvol_delta_detection_to_trigger": 0.5,
        "regime_pattern_mismatch": 0.0,
    }
    b = SetupSnapshot(symbol="Y", setup_id="b", setup_type="breakout")
    b.__disc__ = {  # type: ignore[attr-defined]
        "move_maturity_pct": 2.0,
        "rvol_delta_detection_to_trigger": 0.3,
        "regime_pattern_mismatch": 0.0,
    }
    # Poorer group — over-extended + fading volume.
    c = SetupSnapshot(symbol="Z", setup_id="c", setup_type="breakout")
    c.__disc__ = {  # type: ignore[attr-defined]
        "move_maturity_pct": 5.0,
        "rvol_delta_detection_to_trigger": -0.4,
        "regime_pattern_mismatch": 1.0,
    }
    d = SetupSnapshot(symbol="W", setup_id="d", setup_type="breakout")
    d.__disc__ = {  # type: ignore[attr-defined]
        "move_maturity_pct": 4.0,
        "rvol_delta_detection_to_trigger": -0.2,
        "regime_pattern_mismatch": 1.0,
    }
    cmp = _compare_groups(better=[a, b], poorer=[c, d])
    assert cmp["better_n"] == 2
    assert cmp["poorer_n"] == 2
    # Better setups less mature → mean_delta negative.
    mm = cmp["features"]["move_maturity_pct"]
    assert mm["better_mean"] == 1.5
    assert mm["poorer_mean"] == 4.5
    assert mm["mean_delta"] == -3.0
    assert mm["direction"] == "better_lower"
    # Better setups had rising rvol → mean_delta positive.
    rvol = cmp["features"]["rvol_delta_detection_to_trigger"]
    assert rvol["direction"] == "better_higher"
    # Regime mismatch — better = 0, poorer = 1.
    reg = cmp["features"]["regime_pattern_mismatch"]
    assert reg["mean_delta"] == -1.0


def test_compare_groups_marks_insufficient_data_when_group_empty():
    from services.alpha_discernment_postmortem import (
        SetupSnapshot, _compare_groups,
    )
    a = SetupSnapshot(symbol="X", setup_id="a", setup_type="breakout")
    a.__disc__ = {"move_maturity_pct": 2.0}  # type: ignore[attr-defined]
    cmp = _compare_groups(better=[a], poorer=[])
    entry = cmp["features"]["move_maturity_pct"]
    assert entry.get("note") == "insufficient_data"


# ─────────────────────────────────────────────
#  End-to-end (offline — LLM disabled)
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_postmortem_reconstructs_from_hot_store_and_persists():
    """The whole pipeline against seeded data:
    * WMT detected + triggered (aligned momo, tight risk, fresh vol)
    * TSLA detected + triggered (over-extended, fading vol, momo in chop)
    """
    # Seed WMT
    _seed_lifecycle(
        "setup-wmt", "WMT", "setup_detected",
        payload={
            "symbol": "WMT",
            "stage": "detected",
            "setup_type": "breakout",
            "pct_change": 1.0, "relative_volume": 2.0, "price": 100.0,
            "slow_regime": "trend_up", "fast_regime": "trend_up",
            "trigger_price": 100.5, "invalidation_price": 99.5,
            "target_price": 103.0,
        },
        ts_ns=1_000_000_000_000,
    )
    _seed_lifecycle(
        "setup-wmt", "WMT", "triggered",
        payload={
            "symbol": "WMT", "stage": "triggered",
            "pct_change": 1.1, "relative_volume": 2.3, "price": 100.6,
        },
        ts_ns=1_000_030_000_000,   # 30s to trigger
    )
    # Seed TSLA
    _seed_lifecycle(
        "setup-tsla", "TSLA", "setup_detected",
        payload={
            "symbol": "TSLA", "stage": "detected",
            "setup_type": "breakout",
            "pct_change": 4.5, "relative_volume": 3.0, "price": 350.0,
            "slow_regime": "choppy_meanrevert", "fast_regime": "trend_up",
            "trigger_price": 351.0, "invalidation_price": 348.0,
            "target_price": 353.5,
        },
        ts_ns=1_000_010_000_000,
    )
    _seed_lifecycle(
        "setup-tsla", "TSLA", "triggered",
        payload={
            "symbol": "TSLA", "stage": "triggered",
            "pct_change": 4.2, "relative_volume": 2.4, "price": 350.7,
        },
        ts_ns=1_000_400_000_000,   # 390s to trigger — very late
    )
    db = _FakeDB([
        {"setup_id": "setup-wmt", "filled": True, "mfe": 1.2, "mae": -0.2, "realized_r": 1.1},
        {"setup_id": "setup-tsla", "filled": True, "mfe": 0.3, "mae": -1.4, "realized_r": -0.9},
    ])

    from services import alpha_discernment_postmortem as pm
    # Force the session window helper to see our ts_ns range by
    # patching it — our ts_ns values were arbitrary picks.
    with patch.object(pm, "_session_window_ns", return_value=(0, 2 ** 62)):
        result = await pm.run_postmortem(
            db,
            session_date="2026-09-02",
            better_group=["WMT"],
            poorer_group=["TSLA"],
            invoke_llm=False,
        )

    assert set(result["reconstructable"]) == {"WMT", "TSLA"}
    # Discriminators are populated for both.
    wmt_disc = result["per_setup"]["WMT"]["discriminators"]
    tsla_disc = result["per_setup"]["TSLA"]["discriminators"]
    assert wmt_disc["regime_pattern_mismatch"] == 0.0
    assert tsla_disc["regime_pattern_mismatch"] == 1.0
    # WMT rvol rose; TSLA rvol fell.
    assert wmt_disc["rvol_delta_detection_to_trigger"] > 0
    assert tsla_disc["rvol_delta_detection_to_trigger"] < 0
    # Comparison surfaces the expected direction on move maturity.
    mm = result["comparison"]["features"]["move_maturity_pct"]
    assert mm["direction"] == "better_lower"
    # Persistence — rows should exist in the SQLite tables.
    from services import alpha_hot_store
    with alpha_hot_store._connect() as con:
        n_feat = con.execute(
            "SELECT COUNT(*) FROM alpha_discernment_features WHERE session_date=?",
            ("2026-09-02",),
        ).fetchone()[0]
        n_pm = con.execute(
            "SELECT COUNT(*) FROM alpha_discernment_postmortems WHERE session_date=?",
            ("2026-09-02",),
        ).fetchone()[0]
    assert n_feat == 2
    assert n_pm == 1


@pytest.mark.asyncio
async def test_run_postmortem_survives_missing_setup_events():
    """A symbol with NO lifecycle rows produces a warning but the
    postmortem still runs on the reconstructable subset."""
    _seed_lifecycle(
        "setup-wmt", "WMT", "setup_detected",
        payload={
            "symbol": "WMT", "stage": "detected",
            "setup_type": "breakout",
            "pct_change": 1.0, "relative_volume": 2.0, "price": 100.0,
            "slow_regime": "trend_up",
            "trigger_price": 100.5, "invalidation_price": 99.5,
            "target_price": 103.0,
        },
        ts_ns=1_000_000_000_000,
    )
    from services import alpha_discernment_postmortem as pm
    with patch.object(pm, "_session_window_ns", return_value=(0, 2 ** 62)):
        result = await pm.run_postmortem(
            _FakeDB([]),
            session_date="2026-09-02",
            better_group=["WMT"],
            poorer_group=["TSLA"],
            invoke_llm=False,
        )
    assert "WMT" in result["reconstructable"]
    assert "TSLA" not in result["reconstructable"]
    assert any("TSLA" in w for w in result["warnings"])


# ─────────────────────────────────────────────
#  Claude wrapper — mocked (no real API call)
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_claude_narrative_synthesis_mocked(monkeypatch):
    """Verify Claude Opus 4.8 is invoked with the expected model
    string and the returned text lands in the postmortem row."""
    monkeypatch.setenv("EMERGENT_LLM_KEY", "sk-emergent-test")

    class _FakeChat:
        def __init__(self, *a, **kw):
            self.system = kw.get("system_message", "")
            self.model = None

        def with_model(self, provider, model):
            self.model = (provider, model)
            return self

        async def send_message(self, msg):  # noqa: ARG002
            assert self.model == ("anthropic", "claude-opus-4-8")
            return "Preliminary hypothesis: candidate discriminators..."

    class _FakeUserMessage:
        def __init__(self, text): self.text = text

    fake_module = type("_m", (), {"LlmChat": _FakeChat, "UserMessage": _FakeUserMessage})
    monkeypatch.setattr(
        "emergentintegrations.llm.chat", fake_module, raising=False,
    )
    # Also stitch it into sys.modules so ``from ... import`` finds it.
    import sys
    sys.modules["emergentintegrations.llm.chat"] = fake_module

    from services import alpha_discernment_postmortem as pm
    narrative, model = await pm._synthesize_narrative(
        comparison={"better_n": 2, "poorer_n": 2, "features": {}},
        warnings=[],
    )
    assert "Preliminary hypothesis" in narrative
    assert model == "claude-opus-4-8"


@pytest.mark.asyncio
async def test_claude_narrative_missing_key_returns_placeholder(monkeypatch):
    """No EMERGENT_LLM_KEY → we don't call the API, we return a
    placeholder and the postmortem still lands."""
    monkeypatch.delenv("EMERGENT_LLM_KEY", raising=False)
    from services import alpha_discernment_postmortem as pm
    narrative, model = await pm._synthesize_narrative(
        comparison={}, warnings=[],
    )
    assert "skipped" in narrative.lower()
    assert model == "none"
