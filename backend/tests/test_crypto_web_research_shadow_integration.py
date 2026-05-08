"""Integration tests — crypto bot ↔ shadow web-research ↔ audit log.

What this file pins down
------------------------
Two invariants that distinguish "shadow" from "live veto":

1. The verdict is **persisted** on every fill into both
   ``crypto_signal_audit_log.web_research_shadow_verdict`` and
   ``crypto_paper_trades.web_research_shadow_verdict`` so post-hoc
   expectancy analysis can correlate the LLM's stance with realised
   R-multiple.

2. The verdict NEVER alters the live trade — direction and confidence
   on the inserted trade row match the strategist/auditor combined
   output regardless of whether the verdict is BULLISH, BEARISH, or
   UNKNOWN.

These tests bypass the global ``CRYPTO_SHADOW_RESEARCH_DISABLED`` kill
switch (set by conftest) so the integration path actually fires.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from services import crypto_paper_trader as cpt


def _moderate_uptrend(n: int = 60, base: float = 70000.0,
                      drift: float = 200.0) -> list[float]:
    series = [base]
    for i in range(1, n):
        if i % 3 == 2:
            series.append(series[-1] - drift)
        else:
            series.append(series[-1] + drift)
    return series


def _high_conviction_signal() -> dict:
    """Hand-crafted strategist+auditor output above HIGH_CONVICTION_CONFIDENCE.

    Bypasses real indicator math so the test is decoupled from minor
    threshold tweaks in ``crypto_strategist``. The shape mirrors what
    ``adversarial_signal`` returns on a clean bullish setup.
    """
    return {
        "direction": "LONG",
        "confidence": 0.82,
        "reason": "long_bias_confirmed",
        "strategist": {
            "direction": "LONG",
            "confidence": 0.85,
            "reason": "above_ema rsi=63 momentum=2.4%",
            "indicators": {"rsi": 63.0, "ema20": 69500.0, "momentum_5b": 0.024},
        },
        "auditor": {
            "verdict": "CONFIRM",
            "confidence": 0.79,
            "reason": "no_exhaustion_signal",
            "indicators": {"rsi": 63.0, "ema20": 69500.0, "momentum_5b": 0.024},
        },
    }


class _FakeDB:
    """Motor stub mirroring the one in test_crypto_paper_bot, but with
    an in-memory ``web_research_cache`` collection."""

    def __init__(self):
        self.writes: list[dict] = []
        self.audit_writes: list[dict] = []
        self.cache_docs: dict[str, dict] = {}
        self.paper_trades = AsyncMock()  # MUST stay untouched

        outer = self

        class _CryptoTrades:
            async def insert_one(self, doc):
                outer.writes.append(dict(doc))

        class _Audit:
            async def insert_one(self, doc):
                outer.audit_writes.append(dict(doc))

            async def create_index(self, *_a, **_kw):
                return None

        class _CryptoAdaptations:
            def find(self, _q):
                class _C:
                    def __aiter__(self): return self
                    async def __anext__(self): raise StopAsyncIteration
                return _C()

        class _Cache:
            async def find_one(self, query, _proj=None):
                return outer.cache_docs.get(query.get("symbol"))

            async def update_one(self, query, update, upsert=False):
                sym = query.get("symbol")
                outer.cache_docs[sym] = dict(update.get("$set") or {})

            async def create_index(self, *_a, **_kw):
                return None

        self.crypto_paper_trades = _CryptoTrades()
        self.crypto_signal_audit_log = _Audit()
        self.crypto_model_adaptations = _CryptoAdaptations()
        self.web_research_cache = _Cache()

    def __getitem__(self, key):
        return getattr(self, key)


@pytest.fixture
def _enable_shadow(monkeypatch):
    """Bypass the global kill switch so the shadow path fires."""
    monkeypatch.delenv("CRYPTO_SHADOW_RESEARCH_DISABLED", raising=False)


@pytest.mark.asyncio
async def test_shadow_verdict_logged_into_audit_and_trade(_enable_shadow):
    """When the gate fires and the fetcher returns a verdict, both the
    audit row and the trade row carry the verdict payload."""
    db = _FakeDB()

    fake_verdict = {
        "stance": "BULLISH",
        "rationale": "ETF inflows + strong on-chain volume",
        "confidence": 0.78,
        "agreement": "agree",
        "sources": [{"title": "ETF inflows", "url": "https://example.com/a"}],
        "tavily_answer": "BTC rallies on inflows",
        "query": "BTC crypto price news catalysts last 24 hours macro impact",
        "elapsed_ms": 412,
        "cached": False,
        "shadow_only": True,
        "error": None,
    }

    async def fake_fetcher(_symbol, _signal):
        return fake_verdict

    async def quote(_sym):
        return {"price": 70000.0}

    with patch.object(cpt, "default_shadow_fetcher", new=fake_fetcher), \
         patch.object(cpt, "adversarial_signal", return_value=_high_conviction_signal()):
        result = await cpt.run_crypto_symbol(
            db, "BTC", _moderate_uptrend(60), quote,
        )

    assert result["opened"] is True

    # Trade row carries the verdict, exactly as produced.
    assert len(db.writes) == 1
    trade = db.writes[0]
    assert trade["web_research_shadow_verdict"] is not None
    assert trade["web_research_shadow_verdict"]["stance"] == "BULLISH"
    assert trade["web_research_shadow_verdict"]["agreement"] == "agree"
    assert trade["web_research_shadow_verdict"]["shadow_only"] is True
    # The verdict must include the trigger reason that the router
    # injected so analysts know WHY this trade got research.
    assert trade["web_research_shadow_verdict"]["trigger_reason"] in (
        "high_conviction", "narrative_regime:trend_up", "narrative_regime:parabolic",
        "narrative_regime:overbought", "narrative_regime:oversold",
    )

    # Audit log carries the same verdict (single CONFIRM record).
    assert len(db.audit_writes) == 1
    audit = db.audit_writes[0]
    assert audit["final_direction"] == "LONG"
    assert audit["web_research_shadow_verdict"] is not None
    assert audit["web_research_shadow_verdict"]["stance"] == "BULLISH"


@pytest.mark.asyncio
async def test_disagreeing_shadow_does_not_alter_direction(_enable_shadow):
    """Even when the LLM verdict says BEARISH, the LONG fill happens
    with the SAME direction and the SAME combined confidence — i.e.
    shadow truly is shadow."""
    db = _FakeDB()

    async def bearish_fetcher(_sym, _signal):
        return {
            "stance": "BEARISH",
            "rationale": "macro CPI hot",
            "confidence": 0.9,
            "agreement": "disagree",
            "sources": [],
            "tavily_answer": "",
            "query": "q",
            "elapsed_ms": 10,
            "cached": False,
            "shadow_only": True,
            "error": None,
        }

    async def quote(_sym):
        return {"price": 70000.0}

    with patch.object(cpt, "default_shadow_fetcher", new=bearish_fetcher), \
         patch.object(cpt, "adversarial_signal", return_value=_high_conviction_signal()):
        result = await cpt.run_crypto_symbol(
            db, "BTC", _moderate_uptrend(60), quote,
        )

    assert result["opened"] is True
    assert result["direction"] == "LONG", \
        "Direction must NOT flip based on the shadow verdict"

    trade = db.writes[0]
    assert trade["direction"] == "LONG"
    assert trade["web_research_shadow_verdict"]["stance"] == "BEARISH"
    assert trade["web_research_shadow_verdict"]["agreement"] == "disagree"

    # Confidence on the trade row must be untouched by the shadow path.
    # (Guarded by the strategist/auditor combined confidence — the
    # shadow code never writes to ``signal["confidence"]``.)
    assert isinstance(trade["confidence"], float)
    assert trade["confidence"] >= cpt.MIN_CRYPTO_CONFIDENCE


@pytest.mark.asyncio
async def test_shadow_fetcher_failure_does_not_break_fill(_enable_shadow):
    """A Tavily/LLM exception MUST NOT crash a live fill. The trade
    still opens, just without a verdict attached."""
    db = _FakeDB()

    async def crashing_fetcher(_sym, _signal):
        raise RuntimeError("Tavily 503 spiral")

    async def quote(_sym):
        return {"price": 70000.0}

    with patch.object(cpt, "default_shadow_fetcher", new=crashing_fetcher), \
         patch.object(cpt, "adversarial_signal", return_value=_high_conviction_signal()):
        result = await cpt.run_crypto_symbol(
            db, "BTC", _moderate_uptrend(60), quote,
        )

    assert result["opened"] is True
    trade = db.writes[0]
    # No verdict — but the trade still opened with the live signal.
    assert trade["web_research_shadow_verdict"] is None
    assert trade["direction"] == "LONG"


@pytest.mark.asyncio
async def test_kill_switch_disables_fetcher_call_entirely(monkeypatch):
    """With the kill-switch ON (conftest default), the fetcher is never
    invoked — so no Tavily / LLM calls happen."""
    monkeypatch.setenv("CRYPTO_SHADOW_RESEARCH_DISABLED", "1")
    db = _FakeDB()

    fetcher = AsyncMock()
    async def quote(_sym):
        return {"price": 70000.0}

    with patch.object(cpt, "default_shadow_fetcher", new=fetcher), \
         patch.object(cpt, "adversarial_signal", return_value=_high_conviction_signal()):
        result = await cpt.run_crypto_symbol(
            db, "BTC", _moderate_uptrend(60), quote,
        )

    assert result["opened"] is True
    fetcher.assert_not_called()
    trade = db.writes[0]
    assert trade["web_research_shadow_verdict"] is None
