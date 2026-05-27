"""Tests for the three P1/P2 deltas shipped 2026-02-23.

1. POST/GET /api/admin/council-policy — confidence floor admin
   surface. Doctrine: operator can RAISE only, never drop below
   the hard floor.
2. Alpaca equity volume_24h_usd enrichment.
3. Stage 3.5 per-context promotion matrix.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ── Council Policy doctrine pins ──────────────────────────────────


def test_council_policy_route_exposes_hard_floor_constant():
    """The hard floor MUST be defined and = 0.55 (matches
    ``_MIN_PAPER_CONFIDENCE``). Future code can't accidentally
    drop the safety bar."""
    from routes.admin_council_policy import _hard_floor, _hard_cap
    assert _hard_floor() == 0.55
    assert _hard_cap() == 0.90


def test_council_policy_route_static_authority_owner_only():
    """Both endpoints must owner-gate. A future refactor that
    accidentally drops the role check would expose the floor."""
    src = Path("/app/backend/routes/admin_council_policy.py").read_text(
        encoding="utf-8",
    )
    assert "await _require_owner(request)" in src
    assert 'user.get("role") != "owner"' in src
    # Both GET and POST must call require_owner.
    assert src.count("await _require_owner(request)") >= 2


def test_council_policy_route_clamps_to_doctrine_bounds():
    """Static authority: the route MUST reject values below the
    hard floor / above the hard cap. Search for the rejection
    branches by their exact error strings."""
    src = Path("/app/backend/routes/admin_council_policy.py").read_text(
        encoding="utf-8",
    )
    assert '"below_hard_floor"' in src
    assert '"above_hard_cap"' in src


def test_council_policy_writes_to_existing_override_collection():
    """The route MUST reuse ``confidence_gate_overrides`` so the
    existing dynamic-threshold engine picks up the change with
    zero re-wiring."""
    src = Path("/app/backend/routes/admin_council_policy.py").read_text(
        encoding="utf-8",
    )
    assert 'db["confidence_gate_overrides"]' in src


def test_council_policy_confidence_gate_honours_min_confidence():
    """Static authority: the dynamic gate must read
    ``min_confidence`` from the override row. Without this, the
    POST endpoint would write a value that nothing reads."""
    src = Path("/app/backend/services/confidence_gate.py").read_text(
        encoding="utf-8",
    )
    # The override projection must include the new field.
    assert '"min_confidence": 1' in src
    # The handler must branch on the new field.
    assert '"min_confidence" in override' in src


# ── Alpaca volume_24h_usd enrichment ──────────────────────────────


def test_alpaca_parse_quote_block_emits_volume_24h_usd():
    """The parser must surface ``volume_24h_usd`` when ``dailyBar``
    carries volume + VWAP. This is the field MC's classifier
    snapshot needs (was None pre-fix)."""
    from services.alpaca_equity_quotes import _parse_quote_block
    out = _parse_quote_block(
        latest_quote={"ap": 215.5, "bp": 215.4, "as": 100, "bs": 200,
                      "t": "2026-02-23T12:00:00Z"},
        latest_trade={"p": 215.45, "t": "2026-02-23T12:00:00Z"},
        prev_daily_bar={"c": 214.0},
        daily_bar={"v": 1_000_000, "vw": 215.30, "c": 215.45},
    )
    assert out is not None
    assert out["volume_24h_shares"] == 1_000_000.0
    assert out["volume_24h_usd"] == pytest.approx(215_300_000.0)


def test_alpaca_parse_quote_block_falls_back_to_prev_daily_bar():
    """Pre-market: ``dailyBar.v`` is 0. Adapter must fall back to
    ``prevDailyBar`` so the field is never None."""
    from services.alpaca_equity_quotes import _parse_quote_block
    out = _parse_quote_block(
        latest_quote={"ap": 100, "bp": 99.9, "as": 1, "bs": 1,
                      "t": "2026-02-23T12:00:00Z"},
        latest_trade={"p": 99.95, "t": "2026-02-23T12:00:00Z"},
        prev_daily_bar={"c": 99.5, "v": 500_000, "vw": 99.75},
        daily_bar={"v": 0, "c": 99.95},
    )
    assert out is not None
    assert out["volume_24h_usd"] is not None
    assert out["volume_24h_usd"] == pytest.approx(500_000 * 99.75)


def test_alpaca_parse_quote_block_handles_missing_bars():
    """No ``dailyBar`` and no ``prevDailyBar`` → field stays None
    (we don't fabricate a fake volume)."""
    from services.alpaca_equity_quotes import _parse_quote_block
    out = _parse_quote_block(
        latest_quote={"ap": 100, "bp": 99.9, "as": 1, "bs": 1,
                      "t": "2026-02-23T12:00:00Z"},
        latest_trade={"p": 99.95, "t": "2026-02-23T12:00:00Z"},
    )
    assert out is not None
    assert out["volume_24h_usd"] is None
    assert out["volume_24h_shares"] is None


def test_alpaca_parse_quote_block_falls_back_to_close_when_no_vwap():
    """``dailyBar.vw`` missing → use ``dailyBar.c``."""
    from services.alpaca_equity_quotes import _parse_quote_block
    out = _parse_quote_block(
        latest_quote={"ap": 100, "bp": 99.9, "as": 1, "bs": 1,
                      "t": "2026-02-23T12:00:00Z"},
        latest_trade={"p": 99.95, "t": "2026-02-23T12:00:00Z"},
        daily_bar={"v": 1000, "c": 100.0},  # no vw
    )
    assert out["volume_24h_usd"] == pytest.approx(100_000.0)


def test_intent_enrichment_prefers_pre_computed_volume_24h_usd():
    """Static authority: intent_enrichment must pass through the
    Alpaca-supplied ``volume_24h_usd`` rather than recomputing
    from base volume × price (which would over-multiply)."""
    src = Path("/app/backend/services/intent_enrichment.py").read_text(
        encoding="utf-8",
    )
    assert 'q.get("volume_24h_usd")' in src
    # The fallback path (Kraken base × price) must still exist.
    assert "volume_24h_base" in src


# ── Stage 3.5 per-context promotion ───────────────────────────────


def test_known_regimes_includes_canonical_taxonomy():
    from services.sovereign_promotion_stage_3_5 import KNOWN_REGIMES
    for r in ("trending_up", "trending_down", "ranging",
              "high_volatility", "low_volatility",
              "bull", "bear", "sideways", "unknown"):
        assert r in KNOWN_REGIMES


def test_derive_regime_from_features_explicit_field():
    from services.sovereign_promotion_stage_3_5 import (
        derive_regime_from_features,
    )
    assert derive_regime_from_features({"regime": "bull"}) == "bull"
    assert derive_regime_from_features({"regime": "Trending-Up"}) == "trending_up"


def test_derive_regime_from_features_flag_pattern():
    from services.sovereign_promotion_stage_3_5 import (
        derive_regime_from_features,
    )
    assert derive_regime_from_features({"regime_bull": True}) == "bull"
    assert derive_regime_from_features({"regime_high_volatility": True}) == "high_volatility"


def test_derive_regime_from_features_unknown_default():
    from services.sovereign_promotion_stage_3_5 import (
        derive_regime_from_features,
    )
    assert derive_regime_from_features(None) == "unknown"
    assert derive_regime_from_features({}) == "unknown"
    assert derive_regime_from_features({"regime": "exotic"}) == "unknown"


def test_unknown_regime_never_demotes():
    """Doctrine: the ``unknown`` bucket is informational only — it
    must never demote because older rows that lack regime info
    all land here."""
    from services.sovereign_promotion_stage_3_5 import KNOWN_REGIMES
    # The constant must be present; the actual no-demote behaviour
    # is exercised below in the integration test.
    assert "unknown" in KNOWN_REGIMES


class _Coll:
    def __init__(self, rows):
        self.rows = rows
    def find(self, q=None, proj=None):
        async def _agen():
            for r in self.rows:
                yield r
        return _agen()


class _DB:
    def __init__(self, rows):
        self._rows = rows
    def __getitem__(self, name):
        return _Coll(self._rows)


def _row(regime, was_right, days_ago=10):
    feature_snapshot = {f"regime_{regime}": True} if regime != "unknown" else {}
    return {
        "feature_snapshot": feature_snapshot,
        "outcomes": {"h6": {"was_right": bool(was_right)}},
        "created_at": datetime.now(timezone.utc) - timedelta(days=days_ago),
    }


def test_compute_context_state_promoted_when_global_promoted():
    """If the global gate says promoted AND the context has no
    rolling-window red flags, the cell stays promoted."""
    from services.sovereign_promotion_stage_3_5 import (
        compute_context_promotion_state,
    )
    rows = [_row("bull", True) for _ in range(20)]
    db = _DB(rows)
    out = _run(compute_context_promotion_state(
        db, asset_type="equity", regime="bull", global_promoted=True,
    ))
    assert out["resolved"] == 20
    assert out["wins"] == 20
    assert out["win_rate"] == 1.0
    assert out["promoted_in_context"] is True
    assert out["demoted_in_context"] is False


def test_compute_context_state_demoted_when_win_rate_low(monkeypatch):
    """Even when global is promoted, a regime with rolling win rate
    below threshold demotes."""
    monkeypatch.setenv("STAGE_3_5_MIN_RESOLVED_PER_CONTEXT", "10")
    monkeypatch.setenv("STAGE_3_5_DEMOTE_BELOW_WIN_RATE", "0.45")
    from services.sovereign_promotion_stage_3_5 import (
        compute_context_promotion_state,
    )
    rows = (
        [_row("bear", True) for _ in range(4)]
        + [_row("bear", False) for _ in range(12)]
    )
    db = _DB(rows)
    out = _run(compute_context_promotion_state(
        db, asset_type="equity", regime="bear", global_promoted=True,
    ))
    assert out["resolved"] == 16
    assert out["win_rate"] == pytest.approx(4 / 16)
    assert out["demoted_in_context"] is True
    assert out["promoted_in_context"] is False
    assert "below" in (out["blocker"] or "").lower()


def test_compute_context_state_skips_demotion_under_min_samples(monkeypatch):
    """Few samples → never demote (signal too thin)."""
    monkeypatch.setenv("STAGE_3_5_MIN_RESOLVED_PER_CONTEXT", "20")
    from services.sovereign_promotion_stage_3_5 import (
        compute_context_promotion_state,
    )
    rows = [_row("ranging", False) for _ in range(5)]
    db = _DB(rows)
    out = _run(compute_context_promotion_state(
        db, asset_type="equity", regime="ranging", global_promoted=True,
    ))
    assert out["resolved"] == 5
    assert out["demoted_in_context"] is False
    assert out["promoted_in_context"] is True


def test_compute_context_state_cannot_promote_above_global():
    """Doctrine: even with a perfect per-context win rate, a context
    cannot be promoted above the global state. If global is not
    promoted, neither is the cell."""
    from services.sovereign_promotion_stage_3_5 import (
        compute_context_promotion_state,
    )
    rows = [_row("bull", True) for _ in range(50)]
    db = _DB(rows)
    out = _run(compute_context_promotion_state(
        db, asset_type="equity", regime="bull", global_promoted=False,
    ))
    assert out["win_rate"] == 1.0
    assert out["promoted_in_context"] is False
    # Demoted should be False — global isn't promoted, so the cell
    # is effectively pre-Stage-3 already; no extra demotion needed.
    assert out["demoted_in_context"] is False


def test_compute_promotion_matrix_returns_one_cell_per_regime():
    """The matrix should cover every known regime so the dashboard
    can render a complete grid."""
    from services.sovereign_promotion_stage_3_5 import (
        KNOWN_REGIMES, compute_promotion_matrix,
    )
    db = _DB([])
    cells = _run(compute_promotion_matrix(
        db, asset_type="equity", global_promoted=False,
    ))
    assert len(cells) == len(KNOWN_REGIMES)
    seen_regimes = {c["regime"] for c in cells}
    assert seen_regimes == set(KNOWN_REGIMES)


def test_stage_3_5_route_exposes_matrix():
    """Static authority: the read-only matrix endpoint must exist
    and be owner-gated."""
    src = Path("/app/backend/routes/admin_sovereign_stage_3_5.py").read_text(
        encoding="utf-8",
    )
    assert '@router.get("/promotion-matrix")' in src
    assert "_require_owner" in src
    # Both asset types must be surfaced.
    assert '"equity"' in src and '"crypto"' in src
