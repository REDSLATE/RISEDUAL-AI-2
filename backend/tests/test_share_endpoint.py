"""Tests for the social-share endpoint `/api/share/{ticker}`.

Validates the server-rendered OG/Twitter/JSON-LD metadata plus the
SPA-redirect behavior used by link-preview crawlers on X, Facebook,
LinkedIn, WhatsApp, iMessage, Slack, Discord, Telegram, Reddit, Bluesky,
Pinterest, Signal, and Teams.
"""
from __future__ import annotations

import os
import re
import sys

import pytest
from fastapi.testclient import TestClient

# Make sure we import the live FastAPI app from /app/backend.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from server import app  # noqa: E402


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


# ── Core happy path ──────────────────────────────────────────────────────────

def test_share_returns_html_200(client: TestClient) -> None:
    r = client.get("/api/share/AAPL")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")


def test_share_contains_all_required_og_tags(client: TestClient) -> None:
    """OG covers FB, LinkedIn, WhatsApp, iMessage, Slack, Discord, Telegram,
    Reddit, Bluesky, Pinterest, Signal, Teams — one set of tags, every crawler."""
    body = client.get("/api/share/NVDA").text
    for tag in [
        'property="og:type"',
        'property="og:site_name"',
        'property="og:title"',
        'property="og:description"',
        'property="og:image"',
        'property="og:image:width"',
        'property="og:image:height"',
        'property="og:url"',
    ]:
        assert tag in body, f"missing OG tag: {tag}"


def test_share_contains_twitter_card_tags(client: TestClient) -> None:
    body = client.get("/api/share/TSLA").text
    assert 'name="twitter:card" content="summary_large_image"' in body
    for tag in ['name="twitter:title"', 'name="twitter:description"', 'name="twitter:image"']:
        assert tag in body, f"missing twitter tag: {tag}"


def test_share_contains_financial_product_json_ld(client: TestClient) -> None:
    body = client.get("/api/share/MSFT").text
    assert 'application/ld+json' in body
    assert '"@type":"FinancialProduct"' in body
    assert '"brand"' in body and '"provider"' in body


def test_share_ticker_is_uppercased_in_meta(client: TestClient) -> None:
    body = client.get("/api/share/googl").text
    assert "GOOGL" in body
    # Lower-case ticker should not appear in the OG title field.
    assert re.search(r'og:title"\s+content="[^"]*googl', body) is None


def test_share_includes_redirect_to_spa(client: TestClient) -> None:
    body = client.get("/api/share/SPY").text
    assert 'http-equiv="refresh"' in body
    assert '?warroom=SPY' in body
    assert 'window.location.replace' in body


# ── Input sanitisation ───────────────────────────────────────────────────────

def test_share_rejects_overlong_ticker_gracefully(client: TestClient) -> None:
    """9+ char or non-alphanumeric tickers are replaced with the brand fallback
    (RSDU) instead of 4xx — so a malformed share link never shows as broken."""
    r = client.get("/api/share/TOOLONGXX")
    assert r.status_code == 200
    assert "RSDU" in r.text


def test_share_rejects_special_chars(client: TestClient) -> None:
    r = client.get("/api/share/AA-PL")
    assert r.status_code == 200
    # Falls back to brand, the raw input must not appear in meta tags.
    assert "AA-PL" not in r.text


# ── Forwarded-host / canonical URL resolution ────────────────────────────────

def test_share_honors_x_forwarded_host(client: TestClient) -> None:
    """Behind an ingress, canonical/og:url must use the public domain — not
    the cluster-internal one."""
    r = client.get(
        "/api/share/AAPL",
        headers={
            "x-forwarded-proto": "https",
            "x-forwarded-host": "risedual.ai",
        },
    )
    body = r.text
    assert 'og:url" content="https://risedual.ai/api/share/AAPL"' in body
    assert 'canonical" href="https://risedual.ai/api/share/AAPL"' in body
    # Internal hosts must be suppressed in favor of the env fallback / forwarded host.
    assert "cluster" not in body
    assert "localhost" not in body.lower()


def test_share_ignores_cluster_internal_forwarded_host(client: TestClient) -> None:
    """If the forwarded host looks cluster-internal we fall back to the
    PUBLIC_SITE_URL env (defaults to https://risedual.ai)."""
    r = client.get(
        "/api/share/NVDA",
        headers={"x-forwarded-host": "risedual-trading.cluster-3.preview.emergentcf.cloud"},
    )
    assert "cluster-3" not in r.text


# ── HTTP methods ─────────────────────────────────────────────────────────────

def test_share_supports_head(client: TestClient) -> None:
    """Some link-preview bots probe with HEAD before GET."""
    r = client.head("/api/share/AAPL")
    assert r.status_code == 200
