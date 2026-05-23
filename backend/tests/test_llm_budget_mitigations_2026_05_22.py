"""P1 LLM Budget Mitigations — Layer 1 (cache) + Layer 2 (BYO key).

Pins the budget-resilience contract for
``multi_model_hypothesis_service._run_single_model``:

* Hypothesis cache short-circuits the LLM call on TTL hits.
* Cache keys are stable across identical data bundles.
* Stale cache entries (past expires_at) are NOT served.
* Budget-error detection only fires on clear budget/quota strings.
* Direct provider keys are detected from env vars.
* Layer 2 fallback wraps the original primary exception when no
  direct key is configured (so the error path stays honest).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest


# ── Layer 1: Cache ──────────────────────────────────────────────


from services import hypothesis_cache as hc


def test_cache_key_is_stable_across_identical_bundles():
    data = {"news": [{"title": "AAPL beats"}], "world_events": {}}
    k1 = hc.cache_key("AAPL", "alpha", data)
    k2 = hc.cache_key("aapl", "alpha", data)  # case-insensitive
    assert k1 == k2


def test_cache_key_changes_when_data_changes():
    d1 = {"news": [{"title": "A"}]}
    d2 = {"news": [{"title": "B"}]}
    k1 = hc.cache_key("X", "alpha", d1)
    k2 = hc.cache_key("X", "alpha", d2)
    assert k1 != k2


def test_cache_key_changes_per_model():
    data = {"news": []}
    assert hc.cache_key("X", "alpha", data) != hc.cache_key("X", "camaro", data)


# In-memory DB stand-in just for the cache module.

class _C:
    def __init__(self):
        self.docs: list[dict] = []

    async def create_index(self, *a, **k):
        return None

    async def find_one(self, q=None, proj=None):
        for d in self.docs:
            if all(d.get(k) == v for k, v in (q or {}).items()):
                return dict(d)
        return None

    async def update_one(self, q, upd, upsert=False):
        # Find row
        for d in self.docs:
            if all(d.get(k) == v for k, v in q.items()):
                for k, v in upd.get("$set", {}).items():
                    d[k] = v
                return type("R", (), {"matched_count": 1, "upserted_id": None})()
        if upsert:
            d = dict(upd.get("$set", {}))
            self.docs.append(d)
        return type("R", (), {"matched_count": 0, "upserted_id": "u"})()


class _DB:
    def __init__(self):
        self._cs: dict[str, _C] = {}

    def __getitem__(self, name):
        return self._cs.setdefault(name, _C())


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_cache_put_then_get_round_trip():
    # Reset module-level index gate so put/get re-ensures cleanly.
    hc._INDEX_ENSURED = False
    db = _DB()
    _run(hc.put(db, key="k1", payload={"verdict": "BUY", "confidence": 80}))
    out = _run(hc.get(db, key="k1"))
    assert out == {"verdict": "BUY", "confidence": 80}


def test_cache_expired_rows_return_none():
    hc._INDEX_ENSURED = False
    db = _DB()
    _run(hc.put(db, key="k-expire", payload={"verdict": "HOLD"}))
    # Forcibly age the row past expiry.
    db[hc.COLLECTION].docs[0]["expires_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=10)
    )
    out = _run(hc.get(db, key="k-expire"))
    assert out is None


def test_cache_get_returns_none_on_db_none():
    assert _run(hc.get(None, key="anything")) is None


def test_cache_put_swallows_db_failure_silently():
    class _Broken:
        def __getitem__(self, name):
            raise RuntimeError("mongo down")
    # Should not raise.
    _run(hc.put(_Broken(), key="k", payload={"x": 1}))


def test_cache_ttl_respects_env_clamp(monkeypatch):
    monkeypatch.setenv("HYPOTHESIS_CACHE_TTL_SECONDS", "5")
    assert hc._ttl_seconds() == 60  # clamped to 1m minimum
    monkeypatch.setenv("HYPOTHESIS_CACHE_TTL_SECONDS", "999999")
    assert hc._ttl_seconds() == 3600  # clamped to 1h maximum
    monkeypatch.setenv("HYPOTHESIS_CACHE_TTL_SECONDS", "300")
    assert hc._ttl_seconds() == 300


# ── Layer 2: BYO key fallback ───────────────────────────────────


from services import llm_fallback as lf


def test_is_budget_error_fires_on_quota_strings():
    assert lf.is_budget_error(RuntimeError("Budget exceeded for org"))
    assert lf.is_budget_error(Exception("insufficient_quota: 429"))
    assert lf.is_budget_error(ValueError("Rate_limit_exceeded today"))
    assert lf.is_budget_error(Exception(
        "litellm.BudgetExceededError: organisation cap"
    ))


def test_is_budget_error_ignores_unrelated_errors():
    assert not lf.is_budget_error(RuntimeError("Connection reset by peer"))
    assert not lf.is_budget_error(Exception("Timeout after 30s"))
    assert not lf.is_budget_error(None)  # type: ignore[arg-type]


def test_has_direct_key_detects_env(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert lf.has_direct_key("openai") is False
    assert lf.has_direct_key("anthropic") is False
    assert lf.has_direct_key("gemini") is False
    monkeypatch.setenv("OPENAI_API_KEY", "sk-real")
    assert lf.has_direct_key("openai") is True
    assert lf.has_direct_key("openai") is True  # idempotent
    # Empty string still counts as unset.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "  ")
    assert lf.has_direct_key("anthropic") is False


def test_has_direct_key_unknown_provider():
    assert lf.has_direct_key("foo") is False
    assert lf.has_direct_key("") is False


def test_call_direct_unknown_provider_raises():
    with pytest.raises(RuntimeError, match="no direct-fallback adapter"):
        _run(lf.call_direct(
            provider="azure", model="x", system_message="s", user_message="u",
        ))


def test_call_direct_openai_requires_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY not set"):
        _run(lf.call_direct_openai(
            model="gpt-5.2", system_message="s", user_message="u",
        ))


def test_call_with_emergent_then_fallback_returns_primary_on_success():
    """When the Emergent send succeeds, the wrapper MUST return its
    result without invoking the direct fallback at all."""
    async def emergent():
        return "primary-success-text"
    out = _run(lf.call_with_emergent_then_fallback(
        emergent_send=emergent, provider="openai", model="gpt-5.2",
        system_message="s", user_message="u",
    ))
    assert out == "primary-success-text"


def test_call_with_emergent_then_fallback_rebroadcasts_non_budget_errors():
    """A non-budget exception must NOT trigger fallback; it bubbles
    up so the consensus filter drops the vote honestly."""
    async def emergent():
        raise RuntimeError("Connection timeout")
    with pytest.raises(RuntimeError, match="Connection timeout"):
        _run(lf.call_with_emergent_then_fallback(
            emergent_send=emergent, provider="openai", model="gpt-5.2",
            system_message="s", user_message="u",
        ))


def test_call_with_emergent_then_fallback_skips_fallback_without_byo_key(
    monkeypatch,
):
    """A budget error without a configured direct key still raises
    — we never silently swallow budget exhaustion."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    async def emergent():
        raise RuntimeError("Budget exceeded for organisation")
    with pytest.raises(RuntimeError, match="Budget exceeded"):
        _run(lf.call_with_emergent_then_fallback(
            emergent_send=emergent, provider="openai", model="gpt-5.2",
            system_message="s", user_message="u",
        ))


# ── Static authority: hypothesis service wires both layers ──────


def test_hypothesis_service_threads_data_into_single_model():
    from pathlib import Path
    src = Path("/app/backend/services/multi_model_hypothesis_service.py").read_text(
        encoding="utf-8",
    )
    # Cache lookup gated on data presence
    assert "from services import hypothesis_cache as hc" in src
    assert "hc.cache_key(symbol, model_key, data)" in src
    assert "served_from_cache" in src
    # Layer 2 fallback wired (via the resilience wrapper)
    assert "from services.llm_fallback import call_with_emergent_then_fallback" in src
    assert "call_with_emergent_then_fallback(" in src
    # data is threaded through both consensus + single-model paths
    assert "_run_single_model(api_key, mk, symbol, prompt, data=data)" in src
    assert "_run_single_model(api_key, model, symbol, prompt, data=data)" in src
