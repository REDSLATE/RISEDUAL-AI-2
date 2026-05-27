"""Tests for the Options Education Layer (P3, 2026-02-26)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from routes.learn_options import _GLOSSARY, router


@pytest.fixture
def client() -> TestClient:
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_glossary_is_non_empty():
    assert len(_GLOSSARY) >= 20, "Glossary must cover the core options vocabulary"


def test_every_term_has_required_shape():
    for key, doc in _GLOSSARY.items():
        assert key == key.lower(), f"key {key} must be lowercase"
        assert " " not in key, f"key {key} must be a single token"
        assert {"term", "category", "short", "long"} <= set(doc.keys()), (
            f"{key} missing required fields"
        )
        # Tooltip-friendly: short blurb must fit in one sentence-ish.
        assert 10 <= len(doc["short"]) <= 200, f"{key}.short out of bounds"
        assert len(doc["long"]) >= 60, f"{key}.long too short"


def test_core_terms_present():
    must = {"call", "put", "strike", "premium", "delta", "theta", "iv",
            "covered_call", "iron_condor"}
    assert must <= set(_GLOSSARY.keys())


def test_list_endpoint_returns_grouped_payload(client: TestClient):
    r = client.get("/api/learn/options")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == len(_GLOSSARY)
    assert isinstance(body["categories"], list) and len(body["categories"]) >= 3
    assert set(body["terms"].keys()) == set(_GLOSSARY.keys())
    # The category order returned must match first-seen order in the
    # glossary so the UI can render deterministically.
    seen: list[str] = []
    for v in _GLOSSARY.values():
        if v["category"] not in seen:
            seen.append(v["category"])
    assert body["categories"] == seen


def test_term_lookup_happy_path(client: TestClient):
    r = client.get("/api/learn/options/delta")
    assert r.status_code == 200
    body = r.json()
    assert body["key"] == "delta"
    assert body["term"].startswith("Delta")
    assert body["category"] == "Greeks"


def test_term_lookup_is_case_insensitive(client: TestClient):
    r = client.get("/api/learn/options/COVERED_CALL")
    assert r.status_code == 200
    assert r.json()["key"] == "covered_call"


def test_term_lookup_unknown_returns_404(client: TestClient):
    r = client.get("/api/learn/options/nonexistent_term")
    assert r.status_code == 404
    body = r.json()
    assert body["detail"]["error"] == "unknown_term"
