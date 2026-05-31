"""Tripwire coverage for the strategic dissent scorer.

What this covers
----------------
2026-06 wire-up: ``run_scorer_pass`` now invokes BOTH the tactical
and strategic dissent scorers. Strategic answers the unique-value
question: "shadow said HOLD while active was in a directional
position that has now closed — would shadow have ridden the winner
longer (or escaped the loser) past active's close?"

Doctrine pins re-stated by these tests:

  1. Strategic only fires for ``is_dissent=True`` AND
     ``shadow_action="HOLD"`` AND ``active_action`` is directional.
     Other dissent shapes are tactical-only (already covered).
  2. The join key is symbol + opened_at within ±15min of shadow.ts
     + direction axis match. Equity bots store lowercase ``up``/
     ``down``; crypto bots store ``LONG``/``SHORT`` — both must
     resolve to the canonical axis.
  3. SHORT-side dissents must be scored against a SHORT-held
     hypothetical, not LONG. The bare ``compute_strategic_score``
     default of LONG would mis-score SHORT — the wire fixes this
     by passing the canonical direction from the matched trade.
  4. Tier-3 firewall: the strategic scorer READS from
     ``paper_trades`` / ``crypto_paper_trades`` but MUST NOT write
     to them. Existing ``test_shadow_tier3_isolation.py`` enforces
     this collection-level; this file enforces the unit semantics.
  5. Window-not-elapsed dissents are SKIPPED, not failed — the
     scorer re-tries on subsequent ticks once the strategic
     lookahead window has matured past active's close timestamp.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from services.research_shadow import STRATEGIC_LOOKAHEAD_S
from services.research_shadow_scorer import (
    _DIRECTIONAL_ACTIVE,
    _TRADE_DIR_TO_CANONICAL,
    _fetch_strategic_pending,
    _find_matching_closed_trade,
    _score_one_strategic,
    run_scorer_pass,
)


def _shadow_row(**over):
    base = {
        "decision_id": "dec-1",
        "bot_id": "crypto_fleet",
        "symbol": "BTC",
        "asset_type": "crypto",
        "ts": datetime.now(timezone.utc) - timedelta(hours=2),
        "active_action": "SHORT",
        "shadow_action": "HOLD",
        "is_dissent": True,
        "mid_price": 100.0,
        "sim_fill_bps_round_trip": 20,
        "volume_ratio_at_decision": 1.0,
    }
    base.update(over)
    return base


# ── _DIRECTIONAL_ACTIVE + _TRADE_DIR_TO_CANONICAL doctrine ─────────────


def test_directional_active_includes_all_canonical_axes():
    """LONG/SHORT/BUY/SELL — all must round-trip through the join."""
    for d in ("LONG", "SHORT", "BUY", "SELL"):
        assert d in _DIRECTIONAL_ACTIVE


def test_trade_direction_map_covers_equity_and_crypto_vocabularies():
    """Equity bots store ``up``/``down``; crypto bots store
    ``LONG``/``SHORT``. Both must canonicalize to LONG/SHORT."""
    assert _TRADE_DIR_TO_CANONICAL["LONG"] == "LONG"
    assert _TRADE_DIR_TO_CANONICAL["SHORT"] == "SHORT"
    assert _TRADE_DIR_TO_CANONICAL["up"] == "LONG"
    assert _TRADE_DIR_TO_CANONICAL["down"] == "SHORT"
    assert _TRADE_DIR_TO_CANONICAL["BUY"] == "LONG"
    assert _TRADE_DIR_TO_CANONICAL["SELL"] == "SHORT"


def test_strategic_lookahead_default_30min():
    assert STRATEGIC_LOOKAHEAD_S["stock"] == 30 * 60
    assert STRATEGIC_LOOKAHEAD_S["crypto"] == 30 * 60
    # Options keep the 4h baseline mirror of tactical.
    assert STRATEGIC_LOOKAHEAD_S["options"] == 4 * 60 * 60


# ── _fetch_strategic_pending — filter doctrine ─────────────────────────


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def sort(self, *_a, **_kw):
        return self

    def limit(self, *_a, **_kw):
        return self

    def __aiter__(self):
        async def gen():
            for r in self._rows:
                yield r
        return gen()


class _FakeColl:
    def __init__(self, rows=None):
        self._rows = rows or []
        self.find_query = None

    def find(self, query, projection=None):
        self.find_query = query
        return _FakeCursor(self._rows)


class _FakeDB:
    def __init__(self, colls=None):
        self._colls = colls or {}

    def __getitem__(self, name):
        return self._colls.setdefault(name, _FakeColl())


@pytest.mark.asyncio
async def test_fetch_strategic_pending_filters_by_dissent_hold_and_directional():
    coll = _FakeColl(rows=[_shadow_row()])
    db = _FakeDB({"research_shadow_decisions": coll})
    out = await _fetch_strategic_pending(db, batch=10)
    assert len(out) == 1
    q = coll.find_query
    # Spec: only is_dissent=True AND shadow_action=HOLD AND
    # active_action directional AND strategic_score not present.
    assert q["is_dissent"] is True
    assert q["shadow_action"] == "HOLD"
    assert q["strategic_score"] == {"$exists": False}
    assert set(q["active_action"]["$in"]) == set(_DIRECTIONAL_ACTIVE)


@pytest.mark.asyncio
async def test_fetch_strategic_pending_returns_empty_when_db_none():
    out = await _fetch_strategic_pending(None, batch=10)
    assert out == []


@pytest.mark.asyncio
async def test_fetch_strategic_pending_swallows_db_failure():
    class _BoomColl:
        def find(self, *_a, **_kw):
            raise RuntimeError("db on fire")
    db = _FakeDB({"research_shadow_decisions": _BoomColl()})
    out = await _fetch_strategic_pending(db, batch=10)
    assert out == []


# ── _find_matching_closed_trade — join semantics ───────────────────────


class _FakeColl_FindOneList:
    def __init__(self, rows):
        self._rows = rows

    def find(self, query, projection=None):
        self.last_query = query
        rows = self._rows

        class _Q:
            async def to_list(self, _n):
                return rows
        return _Q()


@pytest.mark.asyncio
async def test_find_matching_closed_trade_picks_closest_by_opened_at():
    shadow = _shadow_row(
        symbol="SOL",
        ts=datetime(2026, 4, 27, 18, 10, 34, tzinfo=timezone.utc),
        active_action="SHORT",
        asset_type="crypto",
    )
    # 3 candidates — middle one is closest to shadow.ts
    trades = [
        {"opened_at": datetime(2026, 4, 27, 18, 25, 32),
         "closed_at": datetime(2026, 4, 28, 10, 53, 8),
         "entry_price": 84.64, "exit_price": 83.81, "direction": "SHORT"},
        {"opened_at": datetime(2026, 4, 27, 18, 10, 34),
         "closed_at": datetime(2026, 4, 28, 10, 53, 8),
         "entry_price": 84.48, "exit_price": 83.81, "direction": "SHORT"},
        {"opened_at": datetime(2026, 4, 27, 17, 56, 39),
         "closed_at": datetime(2026, 4, 28, 10, 53, 8),
         "entry_price": 84.56, "exit_price": 83.81, "direction": "SHORT"},
    ]
    db = _FakeDB({"crypto_paper_trades": _FakeColl_FindOneList(trades)})
    out = await _find_matching_closed_trade(db, shadow)
    assert out is not None
    assert out["entry_price"] == 84.48  # closest opened_at
    assert out["direction_canonical"] == "SHORT"
    assert out["collection"] == "crypto_paper_trades"


@pytest.mark.asyncio
async def test_find_matching_closed_trade_filters_by_direction_axis():
    """SHORT shadow must not match a LONG active even if symbol+ts agree."""
    shadow = _shadow_row(
        symbol="ETH",
        ts=datetime(2026, 4, 27, 18, 10, 34, tzinfo=timezone.utc),
        active_action="SHORT",
        asset_type="crypto",
    )
    trades = [
        {"opened_at": datetime(2026, 4, 27, 18, 10, 0),
         "closed_at": datetime(2026, 4, 27, 19, 0, 0),
         "entry_price": 1000.0, "exit_price": 1010.0, "direction": "LONG"},
    ]
    db = _FakeDB({"crypto_paper_trades": _FakeColl_FindOneList(trades)})
    assert await _find_matching_closed_trade(db, shadow) is None


@pytest.mark.asyncio
async def test_find_matching_closed_trade_equity_uses_ticker_field():
    """Equity bots store symbol on the ``ticker`` field, not ``symbol``."""
    shadow = _shadow_row(
        symbol="NVDA",
        ts=datetime(2026, 4, 27, 18, 10, 34, tzinfo=timezone.utc),
        active_action="LONG",
        asset_type="stock",
    )
    coll = _FakeColl_FindOneList([])
    db = _FakeDB({"paper_trades": coll})
    await _find_matching_closed_trade(db, shadow)
    assert coll.last_query["ticker"] == "NVDA"
    # Should NOT have queried the crypto collection.
    assert "symbol" not in coll.last_query


@pytest.mark.asyncio
async def test_find_matching_closed_trade_handles_equity_lowercase_direction():
    """Equity ``direction="down"`` must match active_action=SHORT."""
    shadow = _shadow_row(
        symbol="NVDA",
        ts=datetime(2026, 4, 27, 18, 10, 34, tzinfo=timezone.utc),
        active_action="SHORT",
        asset_type="stock",
    )
    trades = [
        {"opened_at": datetime(2026, 4, 27, 18, 10, 0),
         "closed_at": datetime(2026, 4, 27, 19, 0, 0),
         "entry_price": 200.0, "exit_price": 210.0, "direction": "down"},
    ]
    db = _FakeDB({"paper_trades": _FakeColl_FindOneList(trades)})
    out = await _find_matching_closed_trade(db, shadow)
    assert out is not None
    assert out["direction_canonical"] == "SHORT"


# ── _score_one_strategic — end-to-end scoring ──────────────────────────


@pytest.mark.asyncio
async def test_score_one_strategic_skips_when_window_not_elapsed():
    """Active closed 5 seconds ago + 30min lookahead → not yet eligible."""
    shadow = _shadow_row(active_action="LONG")
    closed_at_recent = datetime.now(timezone.utc) - timedelta(seconds=5)
    matched = {
        "entry_price": 100.0, "exit_price": 105.0,
        "closed_at": closed_at_recent,
        "direction_canonical": "LONG", "collection": "crypto_paper_trades",
    }
    with patch(
        "services.research_shadow_scorer._find_matching_closed_trade",
        new=AsyncMock(return_value=matched),
    ):
        out = await _score_one_strategic(None, shadow)
        assert out is False


@pytest.mark.asyncio
async def test_score_one_strategic_skips_when_no_match():
    shadow = _shadow_row()
    with patch(
        "services.research_shadow_scorer._find_matching_closed_trade",
        new=AsyncMock(return_value=None),
    ):
        out = await _score_one_strategic(None, shadow)
        assert out is False


@pytest.mark.asyncio
async def test_score_one_strategic_skips_when_no_later_price():
    """Window elapsed + matched trade but quote provider down → skip."""
    shadow = _shadow_row(active_action="LONG")
    closed_at_old = datetime.now(timezone.utc) - timedelta(hours=2)
    matched = {
        "entry_price": 100.0, "exit_price": 105.0,
        "closed_at": closed_at_old,
        "direction_canonical": "LONG", "collection": "crypto_paper_trades",
    }
    with patch(
        "services.research_shadow_scorer._find_matching_closed_trade",
        new=AsyncMock(return_value=matched),
    ), patch(
        "services.research_shadow_scorer._resolve_later_price",
        new=AsyncMock(return_value=None),
    ):
        out = await _score_one_strategic(None, shadow)
        assert out is False


@pytest.mark.asyncio
async def test_score_one_strategic_long_winner_shadow_was_right():
    """Active was LONG, closed at 105, later price 115. Shadow's "if I had
    held longer" gain past close > active's realized 5pt gain — shadow
    was right to suggest HOLD-and-ride."""
    shadow = _shadow_row(active_action="LONG")
    closed_at_old = datetime.now(timezone.utc) - timedelta(hours=2)
    matched = {
        "entry_price": 100.0, "exit_price": 105.0,
        "closed_at": closed_at_old,
        "direction_canonical": "LONG", "collection": "crypto_paper_trades",
    }
    patch_calls = {}

    async def _fake_patch(db, decision_id, *, strategic_score=None, **_kw):
        patch_calls["decision_id"] = decision_id
        patch_calls["score"] = strategic_score
        return True

    with patch(
        "services.research_shadow_scorer._find_matching_closed_trade",
        new=AsyncMock(return_value=matched),
    ), patch(
        "services.research_shadow_scorer._resolve_later_price",
        new=AsyncMock(return_value=115.0),
    ), patch(
        "services.research_shadow_scorer.patch_scores",
        new=_fake_patch,
    ):
        out = await _score_one_strategic(None, shadow)
    assert out is True
    score = patch_calls["score"]
    assert score is not None
    assert score["shadow_was_right"] is True
    assert score["delta_usd"] > 0
    assert score["matched_active_direction"] == "LONG"
    assert score["matched_collection"] == "crypto_paper_trades"


@pytest.mark.asyncio
async def test_score_one_strategic_short_held_uses_short_direction():
    """Active was SHORT (closed at 95 from entry 100 = win). Later
    price 90 means shadow's "if I had stayed short" hypothetical is
    EVEN BETTER (100 → 90 = +10 short). Delta vs active's +5 realised
    must be positive. The wire passes direction=SHORT to
    compute_strategic_score (not the LONG default), so the math is
    in the right direction."""
    shadow = _shadow_row(active_action="SHORT")
    closed_at_old = datetime.now(timezone.utc) - timedelta(hours=2)
    matched = {
        "entry_price": 100.0, "exit_price": 95.0,
        "closed_at": closed_at_old,
        "direction_canonical": "SHORT", "collection": "crypto_paper_trades",
    }
    captured = {}

    async def _fake_patch(db, decision_id, *, strategic_score=None, **_kw):
        captured["score"] = strategic_score
        return True

    with patch(
        "services.research_shadow_scorer._find_matching_closed_trade",
        new=AsyncMock(return_value=matched),
    ), patch(
        "services.research_shadow_scorer._resolve_later_price",
        new=AsyncMock(return_value=90.0),
    ), patch(
        "services.research_shadow_scorer.patch_scores",
        new=_fake_patch,
    ):
        out = await _score_one_strategic(None, shadow)
    assert out is True
    score = captured["score"]
    # Shadow held SHORT from 100 → later 90 = ~+10 (less fill cost).
    # Active closed at 95 from 100 = ~+5 (less fill cost).
    # Delta must be positive — shadow was right to hold SHORT longer.
    assert score["matched_active_direction"] == "SHORT"
    assert score["shadow_was_right"] is True


@pytest.mark.asyncio
async def test_score_one_strategic_annotates_fill_bps():
    shadow = _shadow_row(
        active_action="LONG", sim_fill_bps_round_trip=18,
        volume_ratio_at_decision=2.0,
    )
    closed_at_old = datetime.now(timezone.utc) - timedelta(hours=2)
    matched = {
        "entry_price": 100.0, "exit_price": 105.0,
        "closed_at": closed_at_old,
        "direction_canonical": "LONG", "collection": "crypto_paper_trades",
    }
    captured = {}

    async def _fake_patch(db, decision_id, *, strategic_score=None, **_kw):
        captured["score"] = strategic_score
        return True

    with patch(
        "services.research_shadow_scorer._find_matching_closed_trade",
        new=AsyncMock(return_value=matched),
    ), patch(
        "services.research_shadow_scorer._resolve_later_price",
        new=AsyncMock(return_value=110.0),
    ), patch(
        "services.research_shadow_scorer.patch_scores",
        new=_fake_patch,
    ):
        await _score_one_strategic(None, shadow)
    score = captured["score"]
    assert "fill_cost_bps_applied" in score
    assert score["fill_cost_bps_base"] == 18


# ── run_scorer_pass — integration ──────────────────────────────────────


@pytest.mark.asyncio
async def test_run_scorer_pass_reports_strategic_in_counter():
    """The counter dict must surface both tactical and strategic
    counts so the operator log line ``[shadow-scorer] tick: ...``
    distinguishes the two pipelines."""
    with patch(
        "services.research_shadow_scorer._fetch_tactical_pending",
        new=AsyncMock(return_value=[]),
    ), patch(
        "services.research_shadow_scorer._fetch_strategic_pending",
        new=AsyncMock(return_value=[]),
    ):
        out = await run_scorer_pass(object(), batch=10)
    assert out["scored_tactical"] == 0
    assert out["scored_strategic"] == 0
    assert "scanned" in out
    assert "scored" in out


@pytest.mark.asyncio
async def test_run_scorer_pass_runs_both_pipelines():
    """If tactical and strategic both have pending work, both must
    fire on the same tick — not one-or-the-other."""
    tactical_row = {"decision_id": "t-1"}
    strategic_row = {"decision_id": "s-1"}

    with patch(
        "services.research_shadow_scorer._fetch_tactical_pending",
        new=AsyncMock(return_value=[tactical_row]),
    ), patch(
        "services.research_shadow_scorer._fetch_strategic_pending",
        new=AsyncMock(return_value=[strategic_row]),
    ), patch(
        "services.research_shadow_scorer._score_one_tactical",
        new=AsyncMock(return_value=True),
    ) as tac, patch(
        "services.research_shadow_scorer._score_one_strategic",
        new=AsyncMock(return_value=True),
    ) as strat:
        out = await run_scorer_pass(object(), batch=10)
    assert tac.await_count == 1
    assert strat.await_count == 1
    assert out["scored_tactical"] == 1
    assert out["scored_strategic"] == 1
    assert out["scored"] == 2


@pytest.mark.asyncio
async def test_run_scorer_pass_none_db_returns_empty_counter():
    out = await run_scorer_pass(None, batch=10)
    assert out["scored_tactical"] == 0
    assert out["scored_strategic"] == 0
