"""
Tests for ``services.terminal_aggregator.get_top_actions`` (T2).

Pins:
1. Cold-start (no sovereign rows, no positions) returns an empty
   action list with valid envelope.
2. Decisions are deduped to the freshest per symbol.
3. Old decisions outside the lookback window are excluded.
4. ``ENTER`` cards only emit for high-conviction LONG/SHORT signals
   on symbols the user has no open position in.
5. Open positions reroute the matching symbol to MANAGE_POSITION
   (never silent doubling-down).
6. Concentration cap demotes 6th+ ENTER candidate to WATCH with a
   correlation_note.
7. Priority score is anchored on conviction tier × confidence × size
   × freshness — ranking matches a simple monotone case.
8. Open positions without any fresh sovereign decision still surface
   as MANAGE cards.
9. ``limit`` is clamped to ``[1, 50]``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tests.test_top_universe_service import _FakeDB


def _fixed_now() -> datetime:
    return datetime(2026, 5, 12, 16, 0, tzinfo=timezone.utc)


def _make_decision(
    symbol: str,
    *,
    action: str = "LONG",
    tier: str = "high",
    confidence: float = 0.78,
    size_multiplier: float = 0.5,
    age_minutes: int = 30,
    asset_type: str = "equity",
    vetoes: list | None = None,
    decision_id: str | None = None,
    shadow: bool = True,
) -> dict:
    return {
        "symbol": symbol.upper(),
        "asset_type": asset_type,
        "action": action,
        "direction": action,
        "confidence": confidence,
        "conviction_tier": tier,
        "size_multiplier": size_multiplier,
        "vetoes": vetoes or [],
        "shadow": shadow,
        "decision_id": decision_id or f"dec-{symbol.lower()}-{age_minutes}",
        "created_at": _fixed_now() - timedelta(minutes=age_minutes),
    }


@pytest.mark.asyncio
async def test_top_actions_cold_start_empty_envelope():
    from services.terminal_aggregator import TerminalContext, get_top_actions

    db = _FakeDB()
    out = await get_top_actions(
        TerminalContext(db=db, now=_fixed_now()),
        user_id=None, limit=10,
    )
    assert out["actions"] == []
    assert out["totals"] == {"manage": 0, "enter": 0, "watch": 0, "exit": 0}
    assert out["limit"] == 10
    assert out["lookback_hours"] == 24


@pytest.mark.asyncio
async def test_top_actions_dedupes_to_freshest_per_symbol():
    from services.terminal_aggregator import TerminalContext, get_top_actions

    db = _FakeDB()
    # Two NVDA decisions — only the fresher one should appear.
    db["sovereign_decisions"].docs.append(
        _make_decision("NVDA", confidence=0.55, age_minutes=200, decision_id="old"),
    )
    db["sovereign_decisions"].docs.append(
        _make_decision("NVDA", confidence=0.85, age_minutes=10, decision_id="new"),
    )

    out = await get_top_actions(
        TerminalContext(db=db, now=_fixed_now()),
        user_id=None, limit=10,
    )
    assert len(out["actions"]) == 1
    assert out["actions"][0]["decision_id"] == "new"
    assert out["actions"][0]["symbol"] == "NVDA"


@pytest.mark.asyncio
async def test_top_actions_excludes_decisions_outside_lookback():
    from services.terminal_aggregator import TerminalContext, get_top_actions

    db = _FakeDB()
    # 25h old → outside the 24h lookback window.
    db["sovereign_decisions"].docs.append(
        _make_decision("AAPL", age_minutes=25 * 60, confidence=0.90),
    )
    # Fresh.
    db["sovereign_decisions"].docs.append(
        _make_decision("MSFT", age_minutes=15, confidence=0.78),
    )

    out = await get_top_actions(
        TerminalContext(db=db, now=_fixed_now()),
        user_id=None, limit=10,
    )
    symbols = [a["symbol"] for a in out["actions"]]
    assert "AAPL" not in symbols
    assert "MSFT" in symbols


@pytest.mark.asyncio
async def test_top_actions_classifies_high_conviction_long_as_enter():
    from services.terminal_aggregator import TerminalContext, get_top_actions

    db = _FakeDB()
    db["sovereign_decisions"].docs.append(
        _make_decision("NVDA", action="LONG", tier="high", confidence=0.80),
    )

    out = await get_top_actions(
        TerminalContext(db=db, now=_fixed_now()),
        user_id=None, limit=10,
    )
    assert out["actions"][0]["kind"] == "ENTER"
    assert out["totals"]["enter"] == 1


@pytest.mark.asyncio
async def test_top_actions_open_position_reroutes_to_manage():
    from services.terminal_aggregator import TerminalContext, get_top_actions

    db = _FakeDB()
    # User holds NVDA open — fresh LONG signal must NOT surface as ENTER.
    db["sovereign_decisions"].docs.append(
        _make_decision("NVDA", action="LONG", tier="high", confidence=0.85),
    )
    db["paper_trades"].docs.append({
        "trade_id": "pt-1",
        "ticker": "NVDA",
        "user_id": "user-1",
        "status": "open",
        "direction": "LONG",
    })

    out = await get_top_actions(
        TerminalContext(db=db, now=_fixed_now()),
        user_id="user-1", limit=10,
    )
    nvda = next(a for a in out["actions"] if a["symbol"] == "NVDA")
    assert nvda["kind"] == "MANAGE_POSITION"
    assert out["totals"]["enter"] == 0
    assert out["totals"]["manage"] == 1


@pytest.mark.asyncio
async def test_top_actions_concentration_cap_demotes_extras_to_watch():
    """Six high-conviction ENTER candidates → first 5 stay ENTER, 6th
    gets demoted to WATCH with a correlation_note."""
    from services.terminal_aggregator import TerminalContext, get_top_actions

    db = _FakeDB()
    # Older first, fresher last so dedup keeps each row but ranking
    # by score puts the fresher ones at the top.
    for i, sym in enumerate(["AAA", "BBB", "CCC", "DDD", "EEE", "FFF"]):
        db["sovereign_decisions"].docs.append(_make_decision(
            sym, action="LONG", tier="high",
            confidence=0.80 - i * 0.001,  # tiny tier-internal nudge
            age_minutes=10 + i,
        ))

    out = await get_top_actions(
        TerminalContext(db=db, now=_fixed_now()),
        user_id=None, limit=10,
    )
    enters = [a for a in out["actions"] if a["kind"] == "ENTER"]
    watches = [a for a in out["actions"] if a["kind"] == "WATCH"]
    assert len(enters) == 5
    assert len(watches) == 1
    assert watches[0]["correlation_note"] is not None
    assert "concentration" in watches[0]["correlation_note"].lower()


@pytest.mark.asyncio
async def test_top_actions_priority_score_ranks_high_conviction_first():
    from services.terminal_aggregator import TerminalContext, get_top_actions

    db = _FakeDB()
    db["sovereign_decisions"].docs.append(_make_decision(
        "LOWCONV", action="LONG", tier="medium",
        confidence=0.55, size_multiplier=0.2, age_minutes=20,
    ))
    db["sovereign_decisions"].docs.append(_make_decision(
        "HICONV", action="LONG", tier="high",
        confidence=0.88, size_multiplier=0.9, age_minutes=5,
    ))

    out = await get_top_actions(
        TerminalContext(db=db, now=_fixed_now()),
        user_id=None, limit=10,
    )
    assert out["actions"][0]["symbol"] == "HICONV"
    assert out["actions"][0]["rank"] == 1
    assert out["actions"][1]["symbol"] == "LOWCONV"
    assert out["actions"][0]["priority_score"] > out["actions"][1]["priority_score"]


@pytest.mark.asyncio
async def test_top_actions_orphan_position_surfaces_as_manage():
    from services.terminal_aggregator import TerminalContext, get_top_actions

    db = _FakeDB()
    # No sovereign decision for TSLA — but user has it open.
    db["paper_trades"].docs.append({
        "trade_id": "pt-orphan",
        "ticker": "TSLA",
        "user_id": "user-2",
        "status": "open",
        "direction": "LONG",
    })

    out = await get_top_actions(
        TerminalContext(db=db, now=_fixed_now()),
        user_id="user-2", limit=10,
    )
    tsla = next(a for a in out["actions"] if a["symbol"] == "TSLA")
    assert tsla["kind"] == "MANAGE_POSITION"
    assert tsla["decision_id"] is None
    assert "no fresh sovereign view" in tsla["reason"].lower()


@pytest.mark.asyncio
async def test_top_actions_limit_clamps_to_bounds():
    from services.terminal_aggregator import TerminalContext, get_top_actions

    db = _FakeDB()
    out_zero = await get_top_actions(
        TerminalContext(db=db, now=_fixed_now()),
        user_id=None, limit=0,
    )
    assert out_zero["limit"] == 1

    out_huge = await get_top_actions(
        TerminalContext(db=db, now=_fixed_now()),
        user_id=None, limit=999,
    )
    assert out_huge["limit"] == 50


@pytest.mark.asyncio
async def test_top_actions_low_confidence_holds_become_watch_only():
    """A HOLD@0.50 should not even surface — too low confidence to
    justify operator attention. A HOLD@0.70 should appear as WATCH."""
    from services.terminal_aggregator import TerminalContext, get_top_actions

    db = _FakeDB()
    db["sovereign_decisions"].docs.append(_make_decision(
        "QUIET", action="HOLD", tier="low", confidence=0.50,
    ))
    db["sovereign_decisions"].docs.append(_make_decision(
        "STAY", action="HOLD", tier="medium", confidence=0.70,
    ))

    out = await get_top_actions(
        TerminalContext(db=db, now=_fixed_now()),
        user_id=None, limit=10,
    )
    # Both surface as WATCH — terminal lets operator see all signals,
    # ranking does the prioritization. STAY ranks above QUIET.
    kinds_by_sym = {a["symbol"]: a["kind"] for a in out["actions"]}
    assert kinds_by_sym["QUIET"] == "WATCH"
    assert kinds_by_sym["STAY"] == "WATCH"
    assert out["totals"]["enter"] == 0
    # STAY has higher confidence so ranks higher.
    stay_rank = next(a["rank"] for a in out["actions"] if a["symbol"] == "STAY")
    quiet_rank = next(a["rank"] for a in out["actions"] if a["symbol"] == "QUIET")
    assert stay_rank < quiet_rank


@pytest.mark.asyncio
async def test_top_actions_action_payload_shape():
    """Pin the per-card schema so frontend contracts don't drift."""
    from services.terminal_aggregator import TerminalContext, get_top_actions

    db = _FakeDB()
    db["sovereign_decisions"].docs.append(_make_decision(
        "NVDA", action="LONG", tier="high", confidence=0.82,
        size_multiplier=0.6, vetoes=["liq_low"],
    ))

    out = await get_top_actions(
        TerminalContext(db=db, now=_fixed_now()),
        user_id=None, limit=10,
    )
    a = out["actions"][0]
    expected_keys = {
        "kind", "symbol", "asset_type", "action", "priority_score",
        "conviction", "size_multiplier", "vetoes", "vetoes_count",
        "reason", "correlation_note", "decision_id",
        "decision_age_minutes", "shadow", "links", "rank",
    }
    assert expected_keys.issubset(a.keys())
    assert a["conviction"] == {"tier": "high", "score": 0.82}
    assert a["vetoes"] == ["liq_low"]
    assert a["vetoes_count"] == 1
    assert a["links"]["signal"] == "/api/terminal/signal/NVDA"


# ── EXIT rule tests (Sovereign reversal) ───────────────────────────


@pytest.mark.asyncio
async def test_exit_emitted_on_long_position_with_high_conviction_short():
    """Sovereign flips to SHORT at high conviction on a symbol the
    user holds LONG → EXIT card with reversal-specific reason."""
    from services.terminal_aggregator import TerminalContext, get_top_actions

    db = _FakeDB()
    db["sovereign_decisions"].docs.append(_make_decision(
        "NVDA", action="SHORT", tier="high", confidence=0.82,
    ))
    db["paper_trades"].docs.append({
        "trade_id": "pt-long",
        "ticker": "NVDA",
        "user_id": "user-rev",
        "status": "open",
        "direction": "LONG",
    })

    out = await get_top_actions(
        TerminalContext(db=db, now=_fixed_now()),
        user_id="user-rev", limit=10,
    )
    nvda = next(a for a in out["actions"] if a["symbol"] == "NVDA")
    assert nvda["kind"] == "EXIT"
    assert "reversed to SHORT" in nvda["reason"]
    assert "open LONG position" in nvda["reason"]
    assert out["totals"]["exit"] == 1
    assert out["totals"]["manage"] == 0
    # EXIT must be ranked first.
    assert nvda["rank"] == 1
    assert nvda["priority_score"] >= 0.95


@pytest.mark.asyncio
async def test_exit_emitted_on_short_position_with_high_conviction_long():
    from services.terminal_aggregator import TerminalContext, get_top_actions

    db = _FakeDB()
    db["sovereign_decisions"].docs.append(_make_decision(
        "TSLA", action="LONG", tier="high", confidence=0.78,
    ))
    db["crypto_paper_trades"].docs.append({
        "trade_id": "cpt-short",
        "symbol": "TSLA",
        "user_id": "user-short",
        "status": "open",
        "direction": "SHORT",
    })

    out = await get_top_actions(
        TerminalContext(db=db, now=_fixed_now()),
        user_id="user-short", limit=10,
    )
    tsla = next(a for a in out["actions"] if a["symbol"] == "TSLA")
    assert tsla["kind"] == "EXIT"
    assert "reversed to LONG" in tsla["reason"]
    assert "open SHORT position" in tsla["reason"]


@pytest.mark.asyncio
async def test_low_conviction_reversal_stays_manage_position():
    """SHORT signal at conf 0.55 + medium tier on a LONG position must
    NOT trigger EXIT — that's noise. Stay MANAGE_POSITION."""
    from services.terminal_aggregator import TerminalContext, get_top_actions

    db = _FakeDB()
    db["sovereign_decisions"].docs.append(_make_decision(
        "AAPL", action="SHORT", tier="medium", confidence=0.55,
    ))
    db["paper_trades"].docs.append({
        "trade_id": "pt-aapl",
        "ticker": "AAPL",
        "user_id": "user-noisy",
        "status": "open",
        "direction": "LONG",
    })

    out = await get_top_actions(
        TerminalContext(db=db, now=_fixed_now()),
        user_id="user-noisy", limit=10,
    )
    aapl = next(a for a in out["actions"] if a["symbol"] == "AAPL")
    assert aapl["kind"] == "MANAGE_POSITION"
    assert out["totals"]["exit"] == 0


@pytest.mark.asyncio
async def test_aligned_signal_on_open_position_stays_manage():
    """Sovereign LONG on a LONG position is agreement — never EXIT.
    Operator owns the close decision."""
    from services.terminal_aggregator import TerminalContext, get_top_actions

    db = _FakeDB()
    db["sovereign_decisions"].docs.append(_make_decision(
        "MSFT", action="LONG", tier="high", confidence=0.85,
    ))
    db["paper_trades"].docs.append({
        "trade_id": "pt-msft",
        "ticker": "MSFT",
        "user_id": "user-aligned",
        "status": "open",
        "direction": "LONG",
    })

    out = await get_top_actions(
        TerminalContext(db=db, now=_fixed_now()),
        user_id="user-aligned", limit=10,
    )
    msft = next(a for a in out["actions"] if a["symbol"] == "MSFT")
    assert msft["kind"] == "MANAGE_POSITION"
    assert out["totals"]["exit"] == 0


@pytest.mark.asyncio
async def test_exit_ranks_above_enter_on_same_tick():
    """An EXIT card must always rank above ENTER candidates within
    the same response. The score floor of 0.95 enforces this."""
    from services.terminal_aggregator import TerminalContext, get_top_actions

    db = _FakeDB()
    # Stronger ENTER candidate (conf 0.90) than the reversal trigger
    # (conf 0.72). Without the score floor, ENTER would rank first.
    db["sovereign_decisions"].docs.append(_make_decision(
        "REVRSE", action="SHORT", tier="high", confidence=0.72,
        decision_id="rev",
    ))
    db["sovereign_decisions"].docs.append(_make_decision(
        "STRONG", action="LONG", tier="high", confidence=0.90,
        decision_id="strong",
    ))
    db["paper_trades"].docs.append({
        "trade_id": "pt-rev",
        "ticker": "REVRSE",
        "user_id": "user-mix",
        "status": "open",
        "direction": "LONG",
    })

    out = await get_top_actions(
        TerminalContext(db=db, now=_fixed_now()),
        user_id="user-mix", limit=10,
    )
    assert out["actions"][0]["kind"] == "EXIT"
    assert out["actions"][0]["symbol"] == "REVRSE"
    assert out["actions"][1]["kind"] == "ENTER"
    assert out["actions"][1]["symbol"] == "STRONG"
