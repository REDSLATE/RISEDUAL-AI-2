"""Pytest coverage for the SSE hypothesis stream endpoint.

Mocks the underlying brain runs + cache so the tests are fast and
offline. The endpoint itself is exercised via FastAPI's TestClient.
"""
from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import routes.hypothesis_stream as stream_mod


@pytest.fixture(autouse=True)
def _reset_sse_app_status() -> None:
    """``sse-starlette`` keeps a global ``AppStatus.should_exit_event``
    bound to whichever event loop first touched it. TestClient spins up
    a new event loop per test, which breaks that binding. Reset it
    before each test so every loop gets a fresh event.
    """
    try:
        from sse_starlette.sse import AppStatus
        AppStatus.should_exit_event = None
    except Exception:
        # Older / future versions may not expose AppStatus; ignore.
        pass
    yield


# ── helpers ────────────────────────────────────────────────────────


def _make_app(monkeypatch: pytest.MonkeyPatch, db=None) -> FastAPI:
    """Build a tiny FastAPI app that only includes the stream router."""
    app = FastAPI()
    app.include_router(stream_mod.router)
    app.state.db = db
    monkeypatch.setenv("EMERGENT_LLM_KEY", "fake-test-key")
    return app


def _parse_sse(body: str) -> list[tuple[str, Any]]:
    """Parse SSE body text into a list of (event_name, payload) tuples."""
    events: list[tuple[str, Any]] = []
    current_event: str | None = None
    for line in body.splitlines():
        if line.startswith("event:"):
            current_event = line[6:].strip()
        elif line.startswith("data:"):
            raw = line[5:].strip()
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                payload = raw
            events.append((current_event or "message", payload))
            current_event = None
    return events


class _FakeCache:
    """Stand-in for AICacheService — captures writes, optional hit."""

    def __init__(self, hit: dict | None = None) -> None:
        self.hit = hit
        self.sets: list[dict] = []

    def build_key(self, namespace: str, **kwargs) -> str:
        return f"{namespace}:" + ":".join(f"{k}={v}" for k, v in sorted(kwargs.items()))

    async def get(self, _key: str):
        return self.hit

    async def set(self, *, cache_key, namespace, data, ttl_seconds, meta):
        self.sets.append({"cache_key": cache_key, "data": data})


# ── route shape ────────────────────────────────────────────────────


def test_route_is_mounted_at_expected_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """The stream route must answer at /api/hypothesis/{symbol}/stream."""
    app = _make_app(monkeypatch)
    paths = {r.path for r in app.routes}
    assert "/api/hypothesis/{symbol}/stream" in paths


def test_route_rejects_empty_symbol(monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty / whitespace symbols must 400, not stream noise."""
    app = _make_app(monkeypatch)
    # Patch the JSON-route's user gate so we don't 401.
    monkeypatch.setattr(
        "routes.ai.get_optional_user", lambda *_a, **_kw: None, raising=False,
    )
    monkeypatch.setattr(
        "routes.ai.is_pro_user", lambda *_a, **_kw: True, raising=False,
    )
    client = TestClient(app)
    r = client.get("/api/hypothesis/%20/stream")
    assert r.status_code == 400


# ── cache fast-path ────────────────────────────────────────────────


def test_stream_emits_cached_hit_immediately(monkeypatch: pytest.MonkeyPatch) -> None:
    """When L1 has a hit, we emit one status + one done and close —
    no LLM call."""
    app = _make_app(monkeypatch)

    cached = {"verdict": "BUY", "confidence": 80, "symbol": "NVDA",
              "model": "Alpha 1.6", "model_key": "alpha"}
    stream_mod.hypothesis_l1_cache.clear()
    stream_mod.hypothesis_l1_cache.set(
        "hypothesis:model=alpha:symbol=NVDA", cached, ttl_seconds=60,
    )

    fake_cache = _FakeCache()
    monkeypatch.setattr(stream_mod, "AICacheService", lambda _db: fake_cache)
    monkeypatch.setattr(
        "routes.ai.get_optional_user",
        lambda *_a, **_kw: type("U", (), {"is_pro": True})(),
        raising=False,
    )
    monkeypatch.setattr(
        "routes.ai.is_pro_user", lambda *_a, **_kw: True, raising=False,
    )

    # If the LLM is called, fail loudly — cache hit must short-circuit.
    async def _should_not_run(*_a, **_kw):
        raise AssertionError("LLM call was made on a cache hit")
    monkeypatch.setattr(stream_mod, "_run_single_model", _should_not_run)

    client = TestClient(app)
    with client.stream("GET", "/api/hypothesis/NVDA/stream?model=alpha") as r:
        body = "".join(chunk for chunk in r.iter_text())

    events = _parse_sse(body)
    # Filter out keepalive pings (sse-starlette default emits "ping" comments).
    names = [n for n, _ in events]
    assert "done" in names
    done_payload = next(p for n, p in events if n == "done")
    assert done_payload["verdict"] == "BUY"
    assert done_payload["_cache"]["hit"] is True
    assert done_payload["_cache"]["layer"] == "l1"


# ── single-brain happy path ────────────────────────────────────────


def test_stream_single_brain_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """A cold cache + single brain → status, brain_done, done events."""
    app = _make_app(monkeypatch)
    stream_mod.hypothesis_l1_cache.clear()

    fake_cache = _FakeCache(hit=None)
    monkeypatch.setattr(stream_mod, "AICacheService", lambda _db: fake_cache)

    async def fake_gather(_db, _sym):
        return {"price": 100, "technicals": {}}

    async def fake_run(api_key, model_key, symbol, prompt):
        return {
            "model": "Alpha 1.6", "model_key": model_key, "symbol": symbol,
            "verdict": "BUY", "confidence": 78, "summary": "alpha says buy",
            "thesis": "trend up", "catalysts": [], "risks": [],
        }

    monkeypatch.setattr(stream_mod, "_gather_market_data", fake_gather)
    monkeypatch.setattr(stream_mod, "_run_single_model", fake_run)
    monkeypatch.setattr(
        "routes.ai.get_optional_user",
        lambda *_a, **_kw: type("U", (), {"is_pro": True})(),
        raising=False,
    )
    monkeypatch.setattr(
        "routes.ai.is_pro_user", lambda *_a, **_kw: True, raising=False,
    )

    client = TestClient(app)
    with client.stream("GET", "/api/hypothesis/NVDA/stream?model=alpha") as r:
        assert r.status_code == 200
        body = "".join(chunk for chunk in r.iter_text())

    events = [(n, p) for n, p in _parse_sse(body)
              if n in {"status", "brain_done", "done", "error"}]
    names = [n for n, _ in events]

    # Sequence: at least one status, then brain_done, then done.
    assert names.count("error") == 0, f"unexpected error: {events}"
    assert "status" in names
    assert names.count("brain_done") == 1
    assert names[-1] == "done"
    done_payload = events[-1][1]
    assert done_payload["verdict"] == "BUY"
    assert done_payload["model_key"] == "alpha"
    # Result was persisted to cache.
    assert len(fake_cache.sets) == 1


# ── consensus happy path ────────────────────────────────────────────


def test_stream_consensus_emits_brain_done_per_brain(monkeypatch: pytest.MonkeyPatch) -> None:
    """All 4 brains must emit a brain_done event before the final done."""
    app = _make_app(monkeypatch)
    stream_mod.hypothesis_l1_cache.clear()

    fake_cache = _FakeCache(hit=None)
    monkeypatch.setattr(stream_mod, "AICacheService", lambda _db: fake_cache)

    async def fake_gather(_db, _sym):
        return {"price": 100, "technicals": {}}

    async def fake_run(api_key, model_key, symbol, prompt):
        return {
            "model": stream_mod.BRAINS[model_key]["label"],
            "model_key": model_key, "symbol": symbol,
            "verdict": "BUY", "confidence": 70,
            "summary": f"{model_key} bullish",
            "catalysts": ["c"], "risks": ["r"],
        }

    monkeypatch.setattr(stream_mod, "_gather_market_data", fake_gather)
    monkeypatch.setattr(stream_mod, "_run_single_model", fake_run)
    monkeypatch.setattr(
        "routes.ai.get_optional_user",
        lambda *_a, **_kw: type("U", (), {"is_pro": True})(),
        raising=False,
    )
    monkeypatch.setattr(
        "routes.ai.is_pro_user", lambda *_a, **_kw: True, raising=False,
    )

    client = TestClient(app)
    with client.stream(
        "GET", "/api/hypothesis/NVDA/stream?model=consensus",
    ) as r:
        assert r.status_code == 200
        body = "".join(chunk for chunk in r.iter_text())

    events = [(n, p) for n, p in _parse_sse(body)
              if n in {"status", "brain_done", "done", "error"}]
    names = [n for n, _ in events]
    assert names.count("error") == 0
    assert names.count("brain_done") == 4  # all 4 brains reported
    assert names[-1] == "done"
    brains_seen = {p["brain"] for n, p in events if n == "brain_done"}
    assert brains_seen == {"alpha", "camaro", "chevelle", "redeye"}
    done_payload = events[-1][1]
    assert done_payload["model_key"] == "consensus"
    assert len(done_payload["individual_results"]) == 4


# ── consensus error tolerance ──────────────────────────────────────


def test_stream_consensus_survives_one_brain_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    """If one brain raises, its brain_done event must mark error=True
    but the run continues and emits done."""
    app = _make_app(monkeypatch)
    stream_mod.hypothesis_l1_cache.clear()

    fake_cache = _FakeCache(hit=None)
    monkeypatch.setattr(stream_mod, "AICacheService", lambda _db: fake_cache)

    async def fake_gather(_db, _sym):
        return {"price": 100}

    async def fake_run(api_key, model_key, symbol, prompt):
        if model_key == "redeye":
            raise RuntimeError("redeye exploded")
        return {
            "model": stream_mod.BRAINS[model_key]["label"],
            "model_key": model_key, "symbol": symbol,
            "verdict": "BUY", "confidence": 70, "summary": "ok",
            "catalysts": [], "risks": [],
        }

    monkeypatch.setattr(stream_mod, "_gather_market_data", fake_gather)
    monkeypatch.setattr(stream_mod, "_run_single_model", fake_run)
    monkeypatch.setattr(
        "routes.ai.get_optional_user",
        lambda *_a, **_kw: type("U", (), {"is_pro": True})(),
        raising=False,
    )
    monkeypatch.setattr(
        "routes.ai.is_pro_user", lambda *_a, **_kw: True, raising=False,
    )

    client = TestClient(app)
    with client.stream(
        "GET", "/api/hypothesis/NVDA/stream?model=consensus",
    ) as r:
        body = "".join(chunk for chunk in r.iter_text())

    events = [(n, p) for n, p in _parse_sse(body)
              if n in {"brain_done", "done"}]
    brain_events = [p for n, p in events if n == "brain_done"]
    redeye_event = next(p for p in brain_events if p["brain"] == "redeye")
    assert redeye_event["error"] is True
    assert events[-1][0] == "done"


# ── pro gating ─────────────────────────────────────────────────────


def test_stream_blocks_premium_brain_for_free_user(monkeypatch: pytest.MonkeyPatch) -> None:
    """Free user requesting Camaro must get an error event, not LLM access."""
    app = _make_app(monkeypatch)
    stream_mod.hypothesis_l1_cache.clear()

    fake_cache = _FakeCache(hit=None)
    monkeypatch.setattr(stream_mod, "AICacheService", lambda _db: fake_cache)
    monkeypatch.setattr(
        "routes.ai.get_optional_user", lambda *_a, **_kw: None, raising=False,
    )
    monkeypatch.setattr(
        "routes.ai.is_pro_user", lambda *_a, **_kw: False, raising=False,
    )

    async def _should_not_run(*_a, **_kw):
        raise AssertionError("LLM call was made for a free user")
    monkeypatch.setattr(stream_mod, "_run_single_model", _should_not_run)

    client = TestClient(app)
    with client.stream(
        "GET", "/api/hypothesis/NVDA/stream?model=camaro",
    ) as r:
        body = "".join(chunk for chunk in r.iter_text())

    events = [(n, p) for n, p in _parse_sse(body)]
    error_events = [p for n, p in events if n == "error"]
    assert len(error_events) == 1
    assert error_events[0]["code"] == "premium_required"


def test_stream_allows_alpha_for_free_user(monkeypatch: pytest.MonkeyPatch) -> None:
    """Free user CAN use Alpha (the free-tier default)."""
    app = _make_app(monkeypatch)
    stream_mod.hypothesis_l1_cache.clear()

    fake_cache = _FakeCache(hit=None)
    monkeypatch.setattr(stream_mod, "AICacheService", lambda _db: fake_cache)

    async def fake_gather(_db, _sym):
        return {"price": 100}

    async def fake_run(api_key, model_key, symbol, prompt):
        return {"model": "Alpha 1.6", "model_key": "alpha",
                "symbol": symbol, "verdict": "BUY", "confidence": 70,
                "summary": "ok", "catalysts": [], "risks": []}

    monkeypatch.setattr(stream_mod, "_gather_market_data", fake_gather)
    monkeypatch.setattr(stream_mod, "_run_single_model", fake_run)
    monkeypatch.setattr(
        "routes.ai.get_optional_user", lambda *_a, **_kw: None, raising=False,
    )
    monkeypatch.setattr(
        "routes.ai.is_pro_user", lambda *_a, **_kw: False, raising=False,
    )

    client = TestClient(app)
    with client.stream("GET", "/api/hypothesis/NVDA/stream?model=alpha") as r:
        body = "".join(chunk for chunk in r.iter_text())

    events = [(n, p) for n, p in _parse_sse(body)]
    names = [n for n, _ in events]
    assert "error" not in names
    assert names[-1] == "done"


def test_stream_emits_error_when_no_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """No EMERGENT_LLM_KEY in env → emit error event, don't crash."""
    app = _make_app(monkeypatch)
    stream_mod.hypothesis_l1_cache.clear()
    monkeypatch.delenv("EMERGENT_LLM_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    fake_cache = _FakeCache(hit=None)
    monkeypatch.setattr(stream_mod, "AICacheService", lambda _db: fake_cache)
    monkeypatch.setattr(
        "routes.ai.get_optional_user",
        lambda *_a, **_kw: type("U", (), {"is_pro": True})(),
        raising=False,
    )
    monkeypatch.setattr(
        "routes.ai.is_pro_user", lambda *_a, **_kw: True, raising=False,
    )

    client = TestClient(app)
    with client.stream("GET", "/api/hypothesis/NVDA/stream?model=alpha") as r:
        body = "".join(chunk for chunk in r.iter_text())

    events = [(n, p) for n, p in _parse_sse(body)]
    error_events = [p for n, p in events if n == "error"]
    assert len(error_events) == 1
    assert error_events[0]["code"] == "no_api_key"


# ── unknown model coercion ─────────────────────────────────────────


def test_stream_coerces_unknown_model_to_alpha(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unknown ``model=foo`` falls through to alpha (matches JSON route)."""
    app = _make_app(monkeypatch)
    stream_mod.hypothesis_l1_cache.clear()

    fake_cache = _FakeCache(hit=None)
    monkeypatch.setattr(stream_mod, "AICacheService", lambda _db: fake_cache)
    seen_model_keys: list[str] = []

    async def fake_gather(_db, _sym):
        return {}

    async def fake_run(api_key, model_key, symbol, prompt):
        seen_model_keys.append(model_key)
        return {"model": "Alpha 1.6", "model_key": model_key,
                "symbol": symbol, "verdict": "HOLD", "confidence": 50,
                "summary": "x", "catalysts": [], "risks": []}

    monkeypatch.setattr(stream_mod, "_gather_market_data", fake_gather)
    monkeypatch.setattr(stream_mod, "_run_single_model", fake_run)
    monkeypatch.setattr(
        "routes.ai.get_optional_user",
        lambda *_a, **_kw: type("U", (), {"is_pro": True})(),
        raising=False,
    )
    monkeypatch.setattr(
        "routes.ai.is_pro_user", lambda *_a, **_kw: True, raising=False,
    )

    client = TestClient(app)
    with client.stream("GET", "/api/hypothesis/NVDA/stream?model=banana") as r:
        list(r.iter_text())  # drain
    assert seen_model_keys == ["alpha"]
