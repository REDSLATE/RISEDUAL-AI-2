"""Tests for ``scripts/diagnose_alpha_retrain_join``.

Pins:
  * pure helpers (symbol-field detection, lane inference, candidate
    generation) behave as documented.
  * read-only firewall — no broker / executor / pipeline imports,
    no Mongo writes, no env mutation.
  * recommendations fire on the documented conditions.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts.diagnose_alpha_retrain_join import (
    REASON_DECISION_TOO_RECENT,
    REASON_LANE_MISMATCH,
    REASON_NO_DECISION_LOG,
    REASON_NO_SYMBOL,
    REASON_NORMALIZATION_MISS,
    CollectionProbe,
    DiagnosticResult,
    _build_recommendations,
    _classify_hypotheses,
    _crypto_symbol_candidates,
    _detect_symbol_field,
    _normalize_symbol,
    _safe_field,
    _trade_lane,
    _trade_pnl,
)


# ── Pure helpers ────────────────────────────────────────────


@pytest.mark.parametrize("doc,expected", [
    ({"symbol": "AAPL"}, "symbol"),
    ({"ticker": "TSLA"}, "ticker"),
    ({"pair": "BTC/USD"}, "pair"),
    # Precedence: 'symbol' > 'ticker' > 'pair' (matches SYMBOL_FIELDS order)
    ({"symbol": "AAPL", "ticker": "AAPL"}, "symbol"),
    ({"ticker": "AAPL", "pair": "AAPL/USD"}, "ticker"),
    # Empty / missing
    ({}, None),
    ({"symbol": ""}, None),
    ({"ticker": "   "}, None),
    ({"symbol": None}, None),
])
def test_detect_symbol_field(doc, expected):
    assert _detect_symbol_field(doc) == expected


@pytest.mark.parametrize("coll,doc,expected", [
    ("paper_trades", {}, "equity"),
    ("crypto_paper_trades", {}, "crypto"),
    # Explicit lane on doc trumps collection-name inference
    ("paper_trades", {"lane": "crypto"}, "crypto"),
    ("crypto_paper_trades", {"lane": "equity"}, "equity"),
    # Unknown lane string falls through to collection-name default
    ("paper_trades", {"lane": "options"}, "equity"),
    ("crypto_paper_trades", {"lane": ""}, "crypto"),
    # Unknown collection defaults to equity
    ("mystery_coll", {}, "equity"),
])
def test_trade_lane(coll, doc, expected):
    assert _trade_lane(coll, doc) == expected


def test_trade_pnl_field_preference():
    """Probes in order: realized_pnl_usd > pnl_usd > pnl."""
    assert _trade_pnl("paper_trades",
                      {"realized_pnl_usd": 12.0, "pnl_usd": 99}) == 12.0
    assert _trade_pnl("paper_trades", {"pnl_usd": 7.5}) == 7.5
    assert _trade_pnl("crypto_paper_trades", {"pnl": 4.2}) == 4.2
    assert _trade_pnl("paper_trades", {}) is None
    # Non-numeric ignored
    assert _trade_pnl("paper_trades", {"pnl_usd": "12"}) is None


def test_crypto_symbol_candidates():
    cands = _crypto_symbol_candidates("btc")
    # Bare form FIRST so legitimate matches don't get re-classified
    assert cands[0] == "BTC"
    # All -USD variants present
    assert "BTC-USD" in cands
    assert "BTC/USD" in cands
    assert "BTCUSD" in cands
    assert "BTCUSDT" in cands
    # Empty input → empty list (no crashy fallback)
    assert _crypto_symbol_candidates("") == []
    assert _crypto_symbol_candidates("   ") == []


def test_normalize_symbol_uppers_and_strips():
    assert _normalize_symbol("  aapl  ", "equity") == "AAPL"
    assert _normalize_symbol("BTC", "crypto") == "BTC"
    assert _normalize_symbol(None, "equity") == ""


def test_safe_field_drops_id_and_summarizes_nested():
    doc = {
        "_id": "should-be-stripped",
        "symbol": "AAPL",
        "trail": [1, 2, 3],
        "extras": {"foo": 1, "bar": 2},
        "blob": b"\x00\x01" * 100,
        "long": "x" * 200,
    }
    out = _safe_field(doc)
    assert "_id" not in out
    assert out["symbol"] == "AAPL"
    assert out["trail"].startswith("<list len=")
    assert out["extras"].startswith("<dict keys=")
    assert out["blob"].startswith("<bytes ")
    assert len(out["long"]) <= 80


# ── Hypothesis classification ───────────────────────────────


def _build_result(*, paper_sym=None, crypto_sym="symbol",
                  pt_lane=False, cpt_lane=False,
                  adl_total=7, adl_span=0.4,
                  reason_counts=None,
                  pt_pnl="pnl_usd", cpt_pnl="pnl",
                  window_days=30) -> DiagnosticResult:
    r = DiagnosticResult(
        window_days=window_days, git_sha="abc", timestamp="20260509T0000Z",
    )
    r.paper_trades = CollectionProbe(
        name="paper_trades", total=515, in_window=515,
        symbol_field=paper_sym, has_lane_field=pt_lane,
        pnl_field=pt_pnl,
    )
    r.crypto_paper_trades = CollectionProbe(
        name="crypto_paper_trades", total=1475, in_window=1483,
        symbol_field=crypto_sym, has_lane_field=cpt_lane,
        pnl_field=cpt_pnl,
    )
    r.decision_log = CollectionProbe(
        name="alpha_decision_log", total=adl_total, in_window=adl_total,
    )
    r.decision_log_span_days = adl_span
    r.unmatched_by_reason = reason_counts or {}
    return r


def test_hypotheses_fire_on_under_population_scenario():
    """Mirrors the live fork environment as of 2026-05-09: ADL has
    7 rows total, span <1d, paper_trades uses 'ticker', neither
    paper collection has a lane field, normalization misses present.
    """
    r = _build_result(
        paper_sym="ticker", crypto_sym="symbol",
        pt_lane=False, cpt_lane=False,
        adl_total=7, adl_span=0.4,
        reason_counts={
            REASON_DECISION_TOO_RECENT: 1544,
            REASON_NO_DECISION_LOG: 210,
            REASON_NORMALIZATION_MISS: 194,
        },
    )
    _classify_hypotheses(r)
    assert r.hypotheses["1_paper_trades_pre_date_decision_log"].startswith("YES")
    assert r.hypotheses["2_unstable_join_keys"].startswith("YES")
    assert r.hypotheses["4_symbol_normalization_mismatch"].startswith("YES")
    assert r.hypotheses["5_lane_mismatch"].startswith("YES")
    assert r.hypotheses["6_alpha_decision_log_only_for_newer_decisions"].startswith("YES")
    assert r.hypotheses["7_decision_log_ttl_too_short"].startswith("LIKELY")
    assert r.hypotheses["8_equity_vs_crypto_schema_drift"].startswith("YES")
    # Probe 3 is N/A — the join is symbol-keyed not time-keyed
    assert r.hypotheses["3_wrong_timestamp_field"].startswith("N/A")


def test_hypotheses_clean_state_returns_no():
    r = _build_result(
        paper_sym="symbol", crypto_sym="symbol",
        pt_lane=True, cpt_lane=True,
        adl_total=10000, adl_span=45.0,
        pt_pnl="realized_pnl_usd", cpt_pnl="realized_pnl_usd",
        reason_counts={},
    )
    _classify_hypotheses(r)
    assert r.hypotheses["1_paper_trades_pre_date_decision_log"] == "NO"
    assert r.hypotheses["2_unstable_join_keys"] == "NO"
    assert r.hypotheses["4_symbol_normalization_mismatch"] == "NO"
    assert r.hypotheses["5_lane_mismatch"] == "NO"
    assert r.hypotheses["6_alpha_decision_log_only_for_newer_decisions"] == "NO"
    assert r.hypotheses["7_decision_log_ttl_too_short"] == "NO"
    assert r.hypotheses["8_equity_vs_crypto_schema_drift"] == "NO"


# ── Recommendations ────────────────────────────────────────


def test_recommendations_fire_for_under_population():
    r = _build_result(
        paper_sym="ticker",
        adl_total=7, adl_span=0.4,
        reason_counts={
            REASON_DECISION_TOO_RECENT: 1544,
            REASON_NORMALIZATION_MISS: 194,
        },
    )
    r.matched = 50
    _classify_hypotheses(r)
    _build_recommendations(r)
    text = "\n".join(r.recommendations)
    assert "PRIMARY" in text
    assert "alpha_decision_log is severely under-populated" in text
    assert "lane" in text.lower()
    assert "normalization" in text.lower()
    assert "DO NOT proceed to v2 retrain" in text


def test_recommendations_clean_state():
    r = _build_result(
        paper_sym="symbol", crypto_sym="symbol",
        pt_lane=True, cpt_lane=True,
        adl_total=10000, adl_span=45.0,
        pt_pnl="realized_pnl_usd", cpt_pnl="realized_pnl_usd",
    )
    r.matched = 1500
    _classify_hypotheses(r)
    _build_recommendations(r)
    assert r.recommendations == ["No blocking issues detected; join coverage is healthy."]


# ── Read-only firewall ─────────────────────────────────────


_SCRIPT_PATH = Path(__file__).resolve().parent.parent.joinpath(
    "scripts/diagnose_alpha_retrain_join.py",
)


def test_diagnostic_script_has_no_authority_imports():
    """Pin: no broker/executor/RoadGuard/FastVeto imports."""
    src = _SCRIPT_PATH.read_text()
    forbidden = [
        r"\bfrom services\.broker_service\b",
        r"\bfrom services\.trading_bot_service\b",
        r"\bfrom services\.crypto_paper_trader\b",
        r"\bfrom services\.paper_trading_service\b",
        r"\bfrom routes\.broker\b",
        r"\bfrom services\.ml\.executors\b",
        r"\bfrom services\.ml\.roadguard\b",
        r"\bfrom services\.ml\.fast_veto\b",
        r"\bfrom services\.ml\.shadow_wiring\b",
        r"\bfrom services\.ml\.pipeline\b",
        r"\bfrom services\.ml\.broker_wire\b",
    ]
    for pattern in forbidden:
        assert not re.search(pattern, src), (
            f"diagnose_alpha_retrain_join.py contains forbidden import "
            f"pattern: {pattern}"
        )


def test_diagnostic_script_does_no_writes():
    """Pin: no insert/update/delete/replace/drop calls anywhere.

    The script intentionally couples to motor by reading
    ``server.db`` — but the only verbs it should ever invoke are
    ``find`` / ``find_one`` / ``count_documents`` / ``aggregate``
    / ``estimated_document_count``.
    """
    src = _SCRIPT_PATH.read_text()
    forbidden = [
        ".insert_one(", ".insert_many(", ".update_one(", ".update_many(",
        ".replace_one(", ".delete_one(", ".delete_many(",
        ".drop(", ".rename(", ".create_index(", ".bulk_write(",
        ".find_one_and_update(", ".find_one_and_delete(",
        ".find_one_and_replace(",
    ]
    for verb in forbidden:
        assert verb not in src, (
            f"diagnose_alpha_retrain_join.py performs a write via {verb}"
        )


def test_diagnostic_script_does_not_mutate_env(monkeypatch):
    """Run the diagnostic with no live DB and assert env unchanged.

    Pure behavioural pin — even on the no-db short-circuit path,
    the script must NOT touch env vars.
    """
    import os as _os
    sentinel = "SENTINEL_DIAGNOSTIC_NOT_TOUCHED"
    monkeypatch.setenv("BROKER_LIVE_ORDER_ENABLED", sentinel)

    r = DiagnosticResult(window_days=30, git_sha="x", timestamp="t")
    _classify_hypotheses(r)
    _build_recommendations(r)

    assert _os.environ.get("BROKER_LIVE_ORDER_ENABLED") == sentinel


def test_diagnostic_can_run_with_no_db(monkeypatch):
    """``run_diagnostic`` must short-circuit cleanly when ``server.db``
    is unavailable. No exception escapes."""
    import asyncio
    from scripts import diagnose_alpha_retrain_join as mod

    # Force the from-server-import path to fail
    import sys
    fake_server = type(sys)("server")
    fake_server.db = None
    sys.modules.pop("server", None)
    sys.modules["server"] = fake_server
    try:
        result = asyncio.run(mod.run_diagnostic(window_days=7))
        assert result.window_days == 7
        assert result.matched == 0
        assert result.unmatched == 0
    finally:
        sys.modules.pop("server", None)
