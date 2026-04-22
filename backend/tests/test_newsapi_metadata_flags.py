"""Tests for the NewsAPI.ai adapter metadata-flag opt-ins.

Verifies that the request sent to newsapi.ai includes the five
metadata flags flagged in the onboarding email
(`includeArticleImage`, `includeArticleConcepts`,
`includeArticleCategories`, `includeSourceRanking`,
`includeSourceImage`) so the War Room UI gets richer cards.

Also verifies the response parser preserves image, concepts,
categories, and source_ranking on the emitted items.
"""
from __future__ import annotations

import os
import pytest

from services.search_war_room.adapters import newsapi as adapter
from services.search_war_room import cache as sw_cache


# ────────────────────────────────────────────────────────────────────────────────
# Fixtures
# ────────────────────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _ensure_api_key(monkeypatch):
    """Force a key so the adapter doesn't early-return 'skipped'."""
    monkeypatch.setenv("NEWSAPI_API_KEY", "test-key")


@pytest.fixture(autouse=True)
def _clear_cache(monkeypatch):
    """Make `get_cached` always miss so the adapter actually hits httpx."""
    monkeypatch.setattr(sw_cache, "get_cached", lambda *a, **kw: None)
    monkeypatch.setattr(sw_cache, "set_cached", lambda *a, **kw: None)
    monkeypatch.setattr(adapter, "get_cached", lambda *a, **kw: None)
    monkeypatch.setattr(adapter, "set_cached", lambda *a, **kw: None)


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict:
        return self._payload


class _FakeClient:
    """Minimal httpx.AsyncClient stand-in that records the last GET call."""

    last_params: dict | None = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url: str, params: dict | None = None):
        _FakeClient.last_params = params
        return _FakeResponse(200, {
            "articles": {
                "results": [
                    {
                        "title": "Apple earnings beat",
                        "url": "https://example.com/aapl",
                        "body": "Apple reported stronger-than-expected Q3 earnings...",
                        "image": "https://img.example.com/aapl.jpg",
                        "dateTime": "2026-02-20T10:00:00Z",
                        "source": {
                            "title": "Reuters",
                            "image": {"url": "https://img.example.com/reuters.png"},
                            "ranking": {"alexaGlobalRank": 250},
                        },
                        "concepts": [
                            {
                                "label": {"eng": "Apple Inc."},
                                "type": "org",
                                "score": 5,
                                "uri": "http://en.wikipedia.org/wiki/Apple_Inc.",
                            },
                            {
                                "label": {"eng": "Earnings"},
                                "type": "event",
                                "score": 4,
                                "uri": "earnings",
                            },
                        ],
                        "categories": [
                            {"label": "business/finance", "uri": "biz/fin"},
                        ],
                    }
                ]
            }
        })


# ════════════════════════════════════════════════════════════════════════════════
# Request shape — opt-in metadata flags
# ════════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_request_sends_all_metadata_flags(monkeypatch):
    _FakeClient.last_params = None
    monkeypatch.setattr("httpx.AsyncClient", _FakeClient)

    await adapter.run(query="AAPL earnings", symbol="AAPL")

    params = _FakeClient.last_params
    assert params is not None
    assert params["includeArticleImage"] == "true"
    assert params["includeArticleConcepts"] == "true"
    assert params["includeArticleCategories"] == "true"
    assert params["includeSourceRanking"] == "true"
    assert params["includeSourceImage"] == "true"


@pytest.mark.asyncio
async def test_request_uses_company_name_for_known_ticker(monkeypatch):
    _FakeClient.last_params = None
    monkeypatch.setattr("httpx.AsyncClient", _FakeClient)

    await adapter.run(query="quarterly earnings", symbol="AAPL")
    # Keyword should be the mapped name ("Apple") not the raw ticker.
    assert _FakeClient.last_params["keyword"] == "Apple"


# ════════════════════════════════════════════════════════════════════════════════
# Response parser — metadata propagated to items
# ════════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_response_parser_preserves_metadata_fields(monkeypatch):
    monkeypatch.setattr("httpx.AsyncClient", _FakeClient)

    out = await adapter.run(query="Apple", symbol="AAPL")

    assert out.status == "ok"
    assert out.items, "expected at least one parsed item"
    item = out.items[0]

    # Core fields
    assert item["title"] == "Apple earnings beat"
    assert item["url"] == "https://example.com/aapl"

    # Metadata: image, source_image, source_ranking
    assert item["image"] == "https://img.example.com/aapl.jpg"
    assert item["source_image"] == "https://img.example.com/reuters.png"
    assert item["source_ranking"] == 250

    # Concepts: top-3, preserves label/type/score
    assert len(item["concepts"]) == 2
    assert item["concepts"][0]["label"] == "Apple Inc."
    assert item["concepts"][0]["type"] == "org"
    assert item["concepts"][0]["score"] == 5

    # Categories: preserved
    assert "business/finance" in item["categories"]


@pytest.mark.asyncio
async def test_skipped_when_no_api_key(monkeypatch):
    monkeypatch.delenv("NEWSAPI_API_KEY", raising=False)
    out = await adapter.run(query="AAPL")
    assert out.status == "skipped"
    assert out.error == "missing_newsapi_api_key"
