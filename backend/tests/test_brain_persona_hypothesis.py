"""Pytest coverage for the brain persona registry + hypothesis swap."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from services.brain_persona_service import (
    BRAINS, PUBLIC_KEYS, brain_runtime, public_brain_summary,
)
import services.multi_model_hypothesis_service as hyp


# ── Persona registry shape ─────────────────────────────────────────


def test_four_brains_are_registered() -> None:
    assert set(BRAINS.keys()) == {"alpha", "camaro", "chevelle", "redeye"}


def test_brain_personas_have_required_fields() -> None:
    for key, brain in BRAINS.items():
        assert "label" in brain and brain["label"], f"{key} missing label"
        assert "runtime" in brain and brain["runtime"] == key
        assert "provider" in brain and brain["provider"]
        assert "model" in brain and brain["model"]
        assert "weight" in brain and 0 < brain["weight"] <= 1
        assert "tagline" in brain and brain["tagline"]
        assert "system_prompt" in brain and len(brain["system_prompt"]) > 100


def test_brain_weights_sum_to_one() -> None:
    total = sum(b["weight"] for b in BRAINS.values())
    assert abs(total - 1.0) < 0.001


def test_brain_labels_are_versioned() -> None:
    """Visible labels include version numbers per operator spec."""
    assert BRAINS["alpha"]["label"] == "Alpha 1.6"
    assert BRAINS["camaro"]["label"] == "Camaro 1.3"
    assert BRAINS["chevelle"]["label"] == "Chevelle 1.3"
    assert BRAINS["redeye"]["label"] == "RedEye 1.3"


def test_underlying_models_are_heterogeneous() -> None:
    """At least two providers represented so consensus has real signal."""
    providers = {b["provider"] for b in BRAINS.values()}
    assert len(providers) >= 2


def test_public_keys_include_consensus() -> None:
    assert "consensus" in PUBLIC_KEYS
    assert PUBLIC_KEYS[-1] == "consensus"


def test_public_brain_summary_omits_runtime_field() -> None:
    """The ``runtime`` code name is operator-side and must NEVER leak."""
    summary = public_brain_summary()
    for item in summary:
        assert "runtime" not in item
        assert "system_prompt" not in item
        # Only the safe-to-expose fields.
        assert set(item.keys()) == {"key", "label", "provider", "weight"}


def test_brain_runtime_helper() -> None:
    assert brain_runtime("alpha") == "alpha"
    assert brain_runtime("camaro") == "camaro"
    assert brain_runtime("consensus") is None
    assert brain_runtime("nonexistent") is None


def test_models_alias_points_at_brains() -> None:
    """Back-compat: legacy code reads ``hyp.MODELS`` — must equal BRAINS."""
    assert hyp.MODELS is BRAINS


# ── MC opinion lookup ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_mc_opinion_handles_disabled_sidecar(monkeypatch) -> None:
    """When MC is unreachable / disabled, returns empty string."""
    async def empty(*_a, **_kw):
        return {"items": [], "count": 0, "error": "sidecar_disabled"}

    monkeypatch.setattr("services.risedual_monorepo_client.read_opinions", empty)
    note = await hyp._fetch_recent_mc_opinion("alpha", "NVDA")
    assert note == ""


@pytest.mark.asyncio
async def test_fetch_mc_opinion_returns_empty_for_consensus_key() -> None:
    """Consensus is not a brain — must short-circuit to empty."""
    note = await hyp._fetch_recent_mc_opinion("consensus", "NVDA")
    assert note == ""


@pytest.mark.asyncio
async def test_fetch_mc_opinion_formats_bullets(monkeypatch) -> None:
    async def with_items(*_a, **_kw):
        return {
            "items": [
                {"stance": "bull", "body": "trend strong on weekly"},
                {"stance": "observation", "body": "council BUY @ 1.0"},
            ],
            "count": 2,
        }

    monkeypatch.setattr("services.risedual_monorepo_client.read_opinions", with_items)
    note = await hyp._fetch_recent_mc_opinion("alpha", "NVDA")
    assert "trend strong on weekly" in note
    assert "(bull)" in note
    assert "(observation)" in note
    assert "internal consistency" in note


@pytest.mark.asyncio
async def test_fetch_mc_opinion_swallows_exceptions(monkeypatch) -> None:
    async def boom(*_a, **_kw):
        raise RuntimeError("MC is on fire")

    monkeypatch.setattr("services.risedual_monorepo_client.read_opinions", boom)
    note = await hyp._fetch_recent_mc_opinion("alpha", "NVDA")
    assert note == ""


# ── Single-brain generation (uses persona prompt + MC note) ────────


@pytest.mark.asyncio
async def test_run_single_model_uses_brain_prompt(monkeypatch) -> None:
    """Verify the brain's system_prompt is wired into LlmChat."""

    seen: dict = {}

    class _FakeChat:
        def __init__(self, *_a, **kwargs):
            seen["system_message"] = kwargs.get("system_message")

        def with_model(self, provider, model):
            seen["provider"] = provider
            seen["model"] = model
            return self

        async def send_message(self, _msg):
            return (
                '{"verdict": "BUY", "confidence": 75, "thesis": "trending up", '
                '"catalysts": [], "risks": [], "summary": "alpha bullish"}'
            )

    monkeypatch.setattr(hyp, "LlmChat", _FakeChat)
    # MC lookup returns empty so we test the pure persona path.
    monkeypatch.setattr(hyp, "_fetch_recent_mc_opinion", AsyncMock(return_value=""))

    out = await hyp._run_single_model(
        api_key="fake", model_key="alpha", symbol="NVDA",
        prompt="hypothesize on NVDA",
    )
    assert out["model"] == "Alpha 1.6"
    assert out["model_key"] == "alpha"
    assert out["symbol"] == "NVDA"
    assert out["verdict"] == "BUY"
    # The persona prompt is inside the system message.
    assert "Alpha 1.6" in seen["system_message"]
    assert "trend-following" in seen["system_message"]
    # The schema reminder is also there.
    assert "VERDICT" in seen["system_message"]
    # Provider + model came from the persona spec.
    assert seen["provider"] == "openai"
    assert seen["model"] == "gpt-5.2"


@pytest.mark.asyncio
async def test_run_single_model_folds_in_mc_note(monkeypatch) -> None:
    captured: dict = {}

    class _FakeChat:
        def __init__(self, *_a, **kwargs):
            captured["system_message"] = kwargs.get("system_message")

        def with_model(self, *_a, **_kw):
            return self

        async def send_message(self, _msg):
            return '{"verdict": "HOLD", "confidence": 50}'

    monkeypatch.setattr(hyp, "LlmChat", _FakeChat)
    monkeypatch.setattr(
        hyp, "_fetch_recent_mc_opinion",
        AsyncMock(return_value="\n\nrecent MC note: (bull) trend confirmed"),
    )

    await hyp._run_single_model(
        api_key="fake", model_key="alpha", symbol="NVDA", prompt="x",
    )
    assert "recent MC note" in captured["system_message"]


# ── End-to-end generate_hypothesis dispatch ────────────────────────


@pytest.mark.asyncio
async def test_generate_hypothesis_dispatches_to_brain(monkeypatch) -> None:
    async def fake_run(api_key, model_key, symbol, prompt, *, data=None):
        return {"model": "Alpha 1.6", "model_key": model_key,
                "symbol": symbol, "verdict": "BUY", "confidence": 80}

    monkeypatch.setattr(hyp, "_run_single_model", fake_run)
    out = await hyp.generate_hypothesis("fake", "NVDA", {}, model="alpha")
    assert out["model_key"] == "alpha"
    assert out["is_pro"] is True


@pytest.mark.asyncio
async def test_generate_hypothesis_coerces_unknown_model(monkeypatch) -> None:
    called_with: dict = {}

    async def fake_run(api_key, model_key, symbol, prompt, *, data=None):
        called_with["model_key"] = model_key
        return {"model": "Alpha 1.6", "model_key": model_key, "symbol": symbol,
                "verdict": "HOLD", "confidence": 50}

    monkeypatch.setattr(hyp, "_run_single_model", fake_run)
    await hyp.generate_hypothesis("fake", "NVDA", {}, model="gpt-99")
    assert called_with["model_key"] == "alpha"  # coerced to free-tier default


@pytest.mark.asyncio
async def test_generate_hypothesis_consensus_runs_all_four(monkeypatch) -> None:
    calls: list[str] = []

    async def fake_run(api_key, model_key, symbol, prompt, *, data=None):
        calls.append(model_key)
        return {
            "model": BRAINS[model_key]["label"],
            "model_key": model_key,
            "symbol": symbol,
            "verdict": "BUY",
            "confidence": 70,
            "summary": f"{model_key} says BUY",
            "catalysts": ["c"], "risks": ["r"],
        }

    monkeypatch.setattr(hyp, "_run_single_model", fake_run)
    out = await hyp.generate_hypothesis("fake", "NVDA", {}, model="consensus")
    # All 4 brains called.
    assert sorted(calls) == ["alpha", "camaro", "chevelle", "redeye"]
    assert out["model_key"] == "consensus"
    assert out["verdict"] == "BUY"
    assert len(out["individual_results"]) == 4
    # Consensus summary includes 4-brain agreement.
    assert "4" in str(out) or out["agreement"] == 100


@pytest.mark.asyncio
async def test_generate_hypothesis_consensus_survives_one_brain_error(monkeypatch) -> None:
    async def fake_run(api_key, model_key, symbol, prompt, *, data=None):
        if model_key == "redeye":
            return {"model": "RedEye 1.3", "model_key": "redeye",
                    "symbol": symbol, "verdict": "ERROR", "confidence": 0,
                    "summary": "failed", "error": True}
        return {"model": BRAINS[model_key]["label"], "model_key": model_key,
                "symbol": symbol, "verdict": "BUY", "confidence": 70,
                "summary": "ok", "catalysts": [], "risks": []}

    monkeypatch.setattr(hyp, "_run_single_model", fake_run)
    out = await hyp.generate_hypothesis("fake", "NVDA", {}, model="consensus")
    assert out["verdict"] == "BUY"
    # All 4 results visible (incl. the errored one).
    assert len(out["individual_results"]) == 4
    errs = [r for r in out["individual_results"] if r.get("error")]
    assert len(errs) == 1 and errs[0]["model_key"] == "redeye"
