"""
Tests for the ``/api/admin/news-shock/ingestion-sparkline`` endpoint.

Pins:
1. Endpoint requires owner auth (401 unauth).
2. Bucket count matches the requested ``hours`` parameter.
3. Per-source per-hour counts include our seeded rows.
4. ``hours=0`` floors to 1; ``hours>168`` caps to 168.

Uses the live running backend at REACT_APP_BACKEND_URL — matches the
project's existing route-test pattern (see test_broker_endpoints.py).
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest
import requests
from pymongo import MongoClient

from conftest_creds import ADMIN_EMAIL, ADMIN_PASSWORD


BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
MONGO_URL = os.environ.get("MONGO_URL")
DB_NAME = os.environ.get("DB_NAME")


def _skip_if_no_env():
    if not BASE_URL or not MONGO_URL or not DB_NAME:
        pytest.skip("REACT_APP_BACKEND_URL / MONGO_URL / DB_NAME not configured")


@pytest.fixture(scope="module")
def auth_token():
    _skip_if_no_env()
    resp = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        timeout=10,
    )
    if resp.status_code != 200:
        pytest.skip(f"Authentication failed: {resp.status_code} {resp.text}")
    body = resp.json()
    return body.get("access_token") or body.get("token")


@pytest.fixture(scope="module")
def auth_headers(auth_token):
    return {
        "Authorization": f"Bearer {auth_token}",
        "Content-Type": "application/json",
    }


@pytest.fixture
def seed_catalyst_events():
    """Seed catalyst_events with rows in the last 24h, both sources,
    in a synthetic ``__test_ingestion__`` namespace via headline so we
    can clean up reliably after each test."""
    _skip_if_no_env()
    client = MongoClient(MONGO_URL)
    db = client[DB_NAME]
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    docs = []
    # Benzinga: 3 articles in current hour, 2 in hour-1, 1 in hour-5
    for i in range(3):
        docs.append({
            "event_id": f"__test_ingestion__:bz:{i}:0",
            "event_time": now,
            "event_type": "NEWS",
            "source": "benzinga",
            "symbol": "AAPL",
            "headline": "__test_ingestion__ benzinga",
            "url": f"https://example.com/bz/{i}",
        })
    for i in range(2):
        docs.append({
            "event_id": f"__test_ingestion__:bz:{i}:1",
            "event_time": now - timedelta(hours=1),
            "event_type": "NEWS",
            "source": "benzinga",
            "symbol": "MSFT",
            "headline": "__test_ingestion__ benzinga",
            "url": f"https://example.com/bz/{i}/1",
        })
    docs.append({
        "event_id": "__test_ingestion__:bz:0:5",
        "event_time": now - timedelta(hours=5),
        "event_type": "NEWS",
        "source": "benzinga",
        "symbol": "NVDA",
        "headline": "__test_ingestion__ benzinga",
        "url": "https://example.com/bz/0/5",
    })
    # Alpha Vantage: 1 article current hour, 1 article hour-3
    docs.append({
        "event_id": "__test_ingestion__:av:0:0",
        "event_time": now,
        "event_type": "NEWS",
        "source": "alpha_vantage",
        "symbol": "AAPL",
        "headline": "__test_ingestion__ alpha_vantage",
        "url": "https://example.com/av/0",
        "sentiment_score": 0.42,
    })
    docs.append({
        "event_id": "__test_ingestion__:av:0:3",
        "event_time": now - timedelta(hours=3),
        "event_type": "NEWS",
        "source": "alpha_vantage",
        "symbol": "TSLA",
        "headline": "__test_ingestion__ alpha_vantage",
        "url": "https://example.com/av/0/3",
        "sentiment_score": -0.12,
    })

    db.catalyst_events.insert_many(docs)
    yield {"now": now, "benzinga_total": 6, "av_total": 2}
    db.catalyst_events.delete_many(
        {"event_id": {"$regex": "^__test_ingestion__:"}},
    )
    client.close()


def test_requires_auth():
    _skip_if_no_env()
    resp = requests.get(
        f"{BASE_URL}/api/admin/news-shock/ingestion-sparkline",
        timeout=10,
    )
    assert resp.status_code == 401


def test_returns_24_buckets(auth_headers, seed_catalyst_events):
    resp = requests.get(
        f"{BASE_URL}/api/admin/news-shock/ingestion-sparkline?hours=24",
        headers=auth_headers,
        timeout=10,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["hours"] == 24
    assert len(body["benzinga"]) == 24
    assert len(body["alpha_vantage"]) == 24
    assert len(body["buckets"]) == 24
    # Our seeded rows should be reflected in the totals (alongside any
    # pre-existing real ingestion).
    assert body["totals"]["benzinga"] >= seed_catalyst_events["benzinga_total"]
    assert body["totals"]["alpha_vantage"] >= seed_catalyst_events["av_total"]
    # Current hour counts include the seeded rows.
    assert body["current_hour"]["benzinga"] >= 3
    assert body["current_hour"]["alpha_vantage"] >= 1


def test_hours_param_bounded(auth_headers):
    """hours=0 floors to 1; hours=999 caps at 168."""
    resp_zero = requests.get(
        f"{BASE_URL}/api/admin/news-shock/ingestion-sparkline?hours=0",
        headers=auth_headers,
        timeout=10,
    )
    assert resp_zero.status_code == 200
    body_zero = resp_zero.json()
    assert body_zero["hours"] == 1
    assert len(body_zero["benzinga"]) == 1

    resp_huge = requests.get(
        f"{BASE_URL}/api/admin/news-shock/ingestion-sparkline?hours=999",
        headers=auth_headers,
        timeout=10,
    )
    assert resp_huge.status_code == 200
    body_huge = resp_huge.json()
    assert body_huge["hours"] == 168
    assert len(body_huge["benzinga"]) == 168
