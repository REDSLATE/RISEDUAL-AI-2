"""Memory Modulator doctrine + P0 safety tests (2026-02-23).

Pins:
  * HOLD never modulated.
  * Confidence clamped to [0.0, 1.0].
  * Modulator value clamped to [-0.25, +0.10] (doctrine bounds).
  * Direction never created.
  * No matches → modulator=0.
  * Prior winners → upweight.
  * Prior losers → downweight.
  * Symmetric: every brain reaches the same code path.
  * RoadGuard / ladder / gates not bypassed (receipt-only effect).
  * P0-1: quarantined memories excluded from similarity match.
  * P0-4: feature normalization clips high-magnitude rogue fields.
  * Wiring: ml_paper_trader stamps the receipt on intent + trade row.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest


# Helper to run async coros without pytest-asyncio.
def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ── In-memory mongo stand-in ──────────────────────────────────────


class _Coll:
    def __init__(self, name: str):
        self.name = name
        self.docs: list[dict] = []

    def find(self, q: dict | None = None, proj: dict | None = None):
        rows = list(self.docs)
        # Minimal query support: ``$gte`` on created_at, equality
        # on top-level fields. Enough for the modulator's loader.
        if q:
            def _match(d):
                for k, v in q.items():
                    if isinstance(v, dict) and "$gte" in v:
                        if d.get(k) is None or d[k] < v["$gte"]:
                            return False
                    elif d.get(k) != v:
                        return False
                return True
            rows = [d for d in rows if _match(d)]

        class _Cur:
            def __init__(self, rs):
                self.rs = iter(rs)
            def __aiter__(self):
                return self
            async def __anext__(self):
                try:
                    return next(self.rs)
                except StopIteration:
                    raise StopAsyncIteration
        return _Cur(rows)

    async def find_one(self, q, proj=None):
        for d in self.docs:
            if all(d.get(k) == v for k, v in q.items()):
                return dict(d)
        return None


class _DB:
    def __init__(self):
        self._c: dict[str, _Coll] = {}
    def __getitem__(self, name):
        return self._c.setdefault(name, _Coll(name))


# ── Doctrine constants + bounds ───────────────────────────────────


def test_module_exports_doctrine_constants():
    from shared.memory_modulator import MAX_UP, MAX_DOWN
    assert MAX_UP == 0.10
    assert MAX_DOWN == -0.25


def test_clamp_modulator_enforces_bounds():
    from shared.memory_modulator import _clamp_modulator
    assert _clamp_modulator(0.50) == 0.10    # over upper bound
    assert _clamp_modulator(-0.99) == -0.25  # over lower bound
    assert _clamp_modulator(0.03) == 0.03    # in-range untouched
    assert _clamp_modulator(float("nan")) == 0.0  # defensive


def test_apply_modulator_clamps_confidence_to_unit_interval():
    from shared.memory_modulator import apply_modulator_to_confidence
    new, _ = apply_modulator_to_confidence(confidence=0.95, modulator_value=0.50)
    assert new == 1.0   # clamped at upper unit
    new, _ = apply_modulator_to_confidence(confidence=0.10, modulator_value=-0.50)
    assert new == 0.0   # clamped at lower unit
    # In-range untouched.
    new, _ = apply_modulator_to_confidence(confidence=0.50, modulator_value=0.05)
    assert new == pytest.approx(0.55)


def test_apply_modulator_re_clamps_value_at_boundary():
    """Even if a buggy caller passes a value outside the doctrine
    bounds, ``apply_modulator_to_confidence`` MUST re-clamp."""
    from shared.memory_modulator import apply_modulator_to_confidence
    _new, applied = apply_modulator_to_confidence(
        confidence=0.5, modulator_value=99.0,
    )
    assert applied == 0.10  # re-clamped to MAX_UP
    _new, applied = apply_modulator_to_confidence(
        confidence=0.5, modulator_value=-99.0,
    )
    assert applied == -0.25  # re-clamped to MAX_DOWN


# ── HOLD never modulated ──────────────────────────────────────────


def test_hold_never_modulated():
    """Operator-mandated tripwire — HOLD MUST short-circuit."""
    from shared.memory_modulator import compute_memory_modulator
    # No db queries happen for HOLD; we pass None to prove the
    # short-circuit fires before the loader.
    out = _run(compute_memory_modulator(
        None, brain="alpha", symbol="AAPL",
        direction="HOLD", features={"macd": 0.5},
    ))
    assert out["value"] == 0.0
    assert out["reason"] == "non_directional"


def test_unknown_direction_treated_as_hold():
    """Any non-{BUY/SELL/SHORT/UP/DOWN} token is non-directional."""
    from shared.memory_modulator import compute_memory_modulator
    out = _run(compute_memory_modulator(
        None, brain="alpha", symbol="AAPL",
        direction="MAYBE", features={"macd": 0.5},
    ))
    assert out["value"] == 0.0
    assert out["reason"] == "non_directional"


def test_no_direction_creation_pure_function():
    """The modulator returns only the receipt — never a direction.
    Pinning the public-API shape so a future refactor can't
    smuggle a 'recommended_direction' field into the receipt."""
    from shared.memory_modulator import compute_memory_modulator
    out = _run(compute_memory_modulator(
        None, brain="alpha", symbol="AAPL",
        direction="up", features={"macd": 0.5},
    ))
    forbidden = {"direction", "recommended_direction", "action",
                 "flip", "override", "new_direction"}
    for k in forbidden:
        assert k not in out, f"receipt MUST NOT expose '{k}'"


# ── Empty / disabled paths ────────────────────────────────────────


def test_disabled_short_circuits(monkeypatch):
    monkeypatch.setenv("MEMORY_MODULATOR_ENABLED", "false")
    from shared.memory_modulator import compute_memory_modulator
    out = _run(compute_memory_modulator(
        _DB(), brain="alpha", symbol="AAPL",
        direction="up", features={"macd": 0.5},
    ))
    assert out["value"] == 0.0
    assert out["reason"] == "disabled"


def test_no_matches_returns_zero(monkeypatch):
    monkeypatch.setenv("MEMORY_MODULATOR_ENABLED", "true")
    from shared.memory_modulator import compute_memory_modulator
    out = _run(compute_memory_modulator(
        _DB(), brain="alpha", symbol="AAPL",
        direction="up", features={"macd": 0.5},
    ))
    assert out["value"] == 0.0
    assert out["reason"] == "no_resolved_memories"


# ── Behaviour: winners upweight, losers downweight ────────────────


def _seed_memory(db, *, brain="alpha", symbol="AAPL", direction="up",
                 features=None, outcome=1, pred_id=None, days_ago=10,
                 trade_extra=None):
    """Seed a matched decision + closed paper_trade pair."""
    log_col = {
        "alpha": "alpha_decision_log", "camaro": "camaro_decision_log",
        "chevelle": "chevelle_decision_log", "redeye": "redeye_decision_log",
    }[brain]
    pred_id = pred_id or f"pred-{symbol}-{outcome}-{days_ago}"
    db[log_col].docs.append({
        "_id": f"d-{pred_id}",
        "symbol": symbol,
        "created_at": datetime.now(timezone.utc) - timedelta(days=days_ago),
        "decision": {
            "direction": direction,
            "features": features or {"macd": 0.5, "rsi": 0.3, "trend": 0.4},
        },
        "prediction_id": pred_id,
    })
    trade = {
        "prediction_id": pred_id, "status": "closed",
        "outcome": outcome, "pnl_pct": 0.02 if outcome == 1 else -0.02,
        "source": "alpaca",
        # Minimum fields the chevelle labeler needs to NOT
        # quarantine: opened_at, closed_at, lane, source, symbol.
        "opened_at": datetime.now(timezone.utc) - timedelta(days=days_ago),
        "closed_at": datetime.now(timezone.utc) - timedelta(days=days_ago - 1),
        "lane": "equity",
        "ticker": symbol, "symbol": symbol,
        "direction": direction,
        "shares": 10, "qty": 10,
        "receipt_type": "real_fill",
    }
    if trade_extra:
        trade.update(trade_extra)
    db["paper_trades"].docs.append(trade)


def test_prior_winners_upweight(monkeypatch):
    """5+ similar winners → small positive nudge bounded by MAX_UP."""
    monkeypatch.setenv("MEMORY_MODULATOR_ENABLED", "true")
    monkeypatch.setenv("MEMORY_MODULATOR_MIN_MATCHES_FOR_UP", "5")
    monkeypatch.setenv("MEMORY_MODULATOR_MIN_MATCHES_FOR_DOWN", "2")
    from shared.memory_modulator import compute_memory_modulator
    db = _DB()
    feats = {"macd": 0.5, "rsi": 0.3, "trend": 0.4}
    for i in range(6):
        _seed_memory(db, features=feats, outcome=1,
                     pred_id=f"win-{i}", days_ago=10 + i)
    out = _run(compute_memory_modulator(
        db, brain="alpha", symbol="AAPL",
        direction="up", features=feats,
    ))
    assert out["matched_winners"] >= 5
    assert out["matched_losers"] == 0
    assert 0.0 < out["value"] <= 0.10
    assert "winners" in out["reason"]


def test_prior_losers_downweight(monkeypatch):
    """2+ similar losers triggers downweight; up to MAX_DOWN bound."""
    monkeypatch.setenv("MEMORY_MODULATOR_ENABLED", "true")
    monkeypatch.setenv("MEMORY_MODULATOR_MIN_MATCHES_FOR_UP", "5")
    monkeypatch.setenv("MEMORY_MODULATOR_MIN_MATCHES_FOR_DOWN", "2")
    from shared.memory_modulator import compute_memory_modulator
    db = _DB()
    feats = {"macd": 0.5, "rsi": 0.3, "trend": 0.4}
    for i in range(3):
        _seed_memory(db, features=feats, outcome=-1,
                     pred_id=f"loss-{i}", days_ago=10 + i)
    out = _run(compute_memory_modulator(
        db, brain="alpha", symbol="AAPL",
        direction="up", features=feats,
    ))
    assert out["matched_losers"] >= 2
    assert -0.25 <= out["value"] < 0.0
    assert "losers" in out["reason"]


def test_losers_take_priority_over_winners(monkeypatch):
    """Doctrine: if BOTH thresholds are met, losers win — the
    downweight is the conservative move."""
    monkeypatch.setenv("MEMORY_MODULATOR_ENABLED", "true")
    monkeypatch.setenv("MEMORY_MODULATOR_MIN_MATCHES_FOR_UP", "5")
    monkeypatch.setenv("MEMORY_MODULATOR_MIN_MATCHES_FOR_DOWN", "2")
    from shared.memory_modulator import compute_memory_modulator
    db = _DB()
    feats = {"macd": 0.5, "rsi": 0.3, "trend": 0.4}
    for i in range(6):
        _seed_memory(db, features=feats, outcome=1,
                     pred_id=f"w-{i}", days_ago=10 + i)
    for i in range(3):
        _seed_memory(db, features=feats, outcome=-1,
                     pred_id=f"l-{i}", days_ago=20 + i)
    out = _run(compute_memory_modulator(
        db, brain="alpha", symbol="AAPL",
        direction="up", features=feats,
    ))
    # Both thresholds met → losers branch dominates.
    assert out["value"] < 0.0


# ── Symmetry across brains ────────────────────────────────────────


def test_symmetric_across_all_four_brains(monkeypatch):
    """Same code path. No per-brain branches. Each brain reads its
    own decision_log collection but reaches the same result given
    the same memory seed."""
    monkeypatch.setenv("MEMORY_MODULATOR_ENABLED", "true")
    monkeypatch.setenv("MEMORY_MODULATOR_MIN_MATCHES_FOR_UP", "5")
    from shared.memory_modulator import compute_memory_modulator
    feats = {"macd": 0.5, "rsi": 0.3, "trend": 0.4}
    seen_values: dict[str, float] = {}
    for brain in ("alpha", "camaro", "chevelle", "redeye"):
        db = _DB()
        for i in range(6):
            _seed_memory(db, brain=brain, features=feats, outcome=1,
                         pred_id=f"{brain}-w-{i}", days_ago=10 + i)
        out = _run(compute_memory_modulator(
            db, brain=brain, symbol="AAPL",
            direction="up", features=feats,
        ))
        seen_values[brain] = out["value"]
    # All four brains reach the same positive modulator under the
    # same data → symmetric by construction.
    vs = list(seen_values.values())
    assert all(v > 0 for v in vs)
    assert max(vs) - min(vs) < 1e-9, seen_values


# ── P0-1: Quarantine filter ───────────────────────────────────────


def test_quarantined_memories_excluded(monkeypatch):
    """Operator P0-1: rows the Chevelle firewall quarantines (e.g.
    missing lane / opened_at — the labeler rejects them) MUST NOT
    influence the modulator. Without this, a poisoned memory could
    nudge confidence."""
    monkeypatch.setenv("MEMORY_MODULATOR_ENABLED", "true")
    monkeypatch.setenv("MEMORY_MODULATOR_MIN_MATCHES_FOR_DOWN", "2")
    from shared.memory_modulator import compute_memory_modulator
    db = _DB()
    feats = {"macd": 0.5, "rsi": 0.3, "trend": 0.4}
    # Seed 3 losers but each is MISSING ``lane`` — the Chevelle
    # firewall will quarantine them. Modulator must skip → neutral.
    for i in range(3):
        pred_id = f"poison-{i}"
        db["alpha_decision_log"].docs.append({
            "_id": f"d-{pred_id}", "symbol": "AAPL",
            "created_at": datetime.now(timezone.utc) - timedelta(days=5),
            "decision": {"direction": "up", "features": feats},
            "prediction_id": pred_id,
        })
        db["paper_trades"].docs.append({
            "prediction_id": pred_id, "status": "closed",
            "outcome": -1,
            # NOTE: ``lane`` is intentionally omitted so the
            # Chevelle firewall quarantines this row.
            "opened_at": datetime.now(timezone.utc) - timedelta(days=5),
            "closed_at": datetime.now(timezone.utc) - timedelta(days=4),
            "ticker": "AAPL", "symbol": "AAPL", "direction": "up",
            "shares": 10, "qty": 10,
        })
    out = _run(compute_memory_modulator(
        db, brain="alpha", symbol="AAPL",
        direction="up", features=feats,
    ))
    # Quarantined rows were skipped → no matches → neutral.
    assert out["matched_losers"] == 0
    assert out["matched_winners"] == 0
    assert out["value"] == 0.0
    assert out["skipped_quarantined"] >= 1


# ── P0-4: Feature normalization ───────────────────────────────────


def test_feature_normalization_clips_rogue_high_magnitudes():
    """A single feature with magnitude 999 must NOT dominate the
    cosine — the modulator clips per-feature to [-3.0, +3.0]."""
    from shared.memory_modulator import _normalise_features
    raw = {"macd": 0.5, "volume_zscore": 9999, "trend": -88}
    norm = _normalise_features(raw)
    assert norm["macd"] == 0.5
    assert norm["volume_zscore"] == 3.0   # clipped to upper
    assert norm["trend"] == -3.0          # clipped to lower


def test_feature_normalization_only_uses_stable_whitelist():
    """An attacker / upstream regression that adds a never-seen
    field MUST NOT slip into the similarity vector."""
    from shared.memory_modulator import _normalise_features
    raw = {"macd": 0.5, "x_secret_backdoor_feature": 999}
    norm = _normalise_features(raw)
    assert "macd" in norm
    assert "x_secret_backdoor_feature" not in norm


def test_feature_normalization_handles_garbage_gracefully():
    """Strings / None / nested dicts default to 0.0 — never crash."""
    from shared.memory_modulator import _normalise_features
    norm = _normalise_features(
        {"macd": "garbage", "rsi": None, "trend": {"nested": 1}}
    )
    assert norm["macd"] == 0.0
    assert norm["rsi"] == 0.0
    assert norm["trend"] == 0.0


# ── Cosine sim sanity ─────────────────────────────────────────────


def test_cosine_identical_returns_one():
    from shared.memory_modulator import _cosine_similarity
    a = {"macd": 0.5, "rsi": 0.3}
    assert _cosine_similarity(a, a) == pytest.approx(1.0)


def test_cosine_orthogonal_returns_zero():
    from shared.memory_modulator import _cosine_similarity
    a = {"macd": 1.0, "rsi": 0.0}
    b = {"macd": 0.0, "rsi": 1.0}
    assert _cosine_similarity(a, b) == pytest.approx(0.0)


def test_cosine_empty_returns_zero():
    from shared.memory_modulator import _cosine_similarity
    assert _cosine_similarity({}, {"macd": 1}) == 0.0
    assert _cosine_similarity({}, {}) == 0.0


# ── Wiring static authority ───────────────────────────────────────


def test_ml_paper_trader_invokes_modulator_before_kelly():
    """The modulator MUST run BEFORE Kelly sizing — otherwise the
    confidence delta wouldn't reach the position-sizer."""
    src = Path("/app/backend/services/ml_paper_trader.py").read_text(
        encoding="utf-8",
    )
    mod_pos = src.find("compute_memory_modulator")
    assert mod_pos > 0
    kelly_pos = src.find("half_kelly_position(")
    # The first half_kelly_position call must come AFTER the
    # modulator import + call.
    assert kelly_pos > mod_pos


def test_ml_paper_trader_stamps_receipt_on_intent_payload():
    """The honest-hold emit must carry ``memory_modulator`` so MC's
    audit + tripwire sees it."""
    src = Path("/app/backend/services/ml_paper_trader.py").read_text(
        encoding="utf-8",
    )
    assert '"memory_modulator": memory_modulator_receipt' in src


def test_ml_paper_trader_stamps_receipt_on_trade_row():
    """Paper trade row must carry the receipt too — so post-hoc
    audits can reconstruct conf-before/conf-after even when the
    intent payload is no longer in MC."""
    src = Path("/app/backend/services/ml_paper_trader.py").read_text(
        encoding="utf-8",
    )
    assert 'trade_doc["memory_modulator"] = memory_modulator_receipt' in src


def test_ml_paper_trader_uses_apply_helper_for_re_clamp():
    """Confidence application must go through the helper (which
    re-clamps the value and the resulting confidence). Direct
    arithmetic would skip the safety net."""
    src = Path("/app/backend/services/ml_paper_trader.py").read_text(
        encoding="utf-8",
    )
    assert "apply_modulator_to_confidence" in src
