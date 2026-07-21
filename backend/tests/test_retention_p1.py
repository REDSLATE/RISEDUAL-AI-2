"""Iteration 182 — Retention & Backlog Purge feature verification.

Covers:
  (a) GET  /api/admin/retention/status           → owner/admin only, schema+rules
  (b) GET  /api/admin/retention/status           → 403 for non-admin
  (c) POST /api/admin/retention/purge            → drains up to batch cap, idempotent
  (d) POST /api/admin/retention/purge            → writes audit row to
      retention_purge_log; lifetime counter increments
  (e) PRESERVED collections survive purge (synthetic old row in
      equity_live_trades still present after)
  (f) Paper-trades preservation: status ∈ {open,active,pending} rows
      NEVER purged even when opened_at is far older than the paper TTL
  (g) Regression: /api/broker/positions/{broker_id} sorted alphabetically,
      /api/workspace/watchlist merges + returns tickers list,
      POST /api/web-intel/war-room completes under 15s.
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import requests
from motor.motor_asyncio import AsyncIOMotorClient

BACKEND_DIR = Path("/app/backend")
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from conftest_creds import (  # noqa: E402
    ADMIN_EMAIL,
    ADMIN_PASSWORD,
    BASE_URL,
    FREE_USER_EMAIL,
    FREE_USER_PASSWORD,
)

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "risedual_db")


# ── fixtures ─────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def admin_session():
    s = requests.Session()
    r = s.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        timeout=30,
    )
    if r.status_code != 200:
        pytest.skip(f"admin login failed: {r.status_code} {r.text[:200]}")
    data = r.json()
    token = data.get("access_token") or data.get("token")
    if token:
        s.headers.update({"Authorization": f"Bearer {token}"})
    s.user_id = data.get("id") or data.get("_id")
    return s


@pytest.fixture(scope="module")
def non_admin_session():
    """Log in (or register) a free user; must not be owner/admin."""
    s = requests.Session()
    r = s.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": FREE_USER_EMAIL, "password": FREE_USER_PASSWORD},
        timeout=30,
    )
    if r.status_code != 200:
        reg = s.post(
            f"{BASE_URL}/api/auth/register",
            json={
                "email": FREE_USER_EMAIL,
                "password": FREE_USER_PASSWORD,
                "name": "Free User",
            },
            timeout=30,
        )
        if reg.status_code not in (200, 201):
            pytest.skip(
                f"could not create non-admin user: "
                f"{reg.status_code} {reg.text[:200]}",
            )
        r = s.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": FREE_USER_EMAIL, "password": FREE_USER_PASSWORD},
            timeout=30,
        )
        if r.status_code != 200:
            pytest.skip(f"non-admin login failed: {r.status_code}")
    tok = r.json().get("access_token") or r.json().get("token")
    if tok:
        s.headers.update({"Authorization": f"Bearer {tok}"})
    role = r.json().get("role")
    if role in ("owner", "admin"):
        pytest.skip(f"free_user actually has admin role={role}; can't test 403")
    return s


def _run_db(coro_factory):
    """Run an async helper in a fresh event loop with a fresh Motor client.

    ``coro_factory`` must be a callable ``(db) -> coroutine``. Creating
    a fresh client bound to a new loop for every helper avoids Motor's
    'Event loop is closed' failure mode across pytest tests.
    """
    loop = asyncio.new_event_loop()
    client = AsyncIOMotorClient(MONGO_URL, io_loop=loop)
    try:
        db = client[DB_NAME]
        return loop.run_until_complete(coro_factory(db))
    finally:
        client.close()
        loop.close()


# ── (a) GET /status shape + rules ────────────────────────────────


class TestRetentionStatusShape:
    def test_status_returns_full_schema(self, admin_session):
        r = admin_session.get(
            f"{BASE_URL}/api/admin/retention/status", timeout=30,
        )
        assert r.status_code == 200, r.text
        d = r.json()
        for k in (
            "retention_hours_default",
            "retention_hours_paper",
            "purge_batch_cap",
            "total_backlog",
            "more_remains",
            "breakdown",
            "preserved_collections",
            "last_purge",
            "total_purged_lifetime",
        ):
            assert k in d, f"missing key: {k}"
        assert d["retention_hours_default"] == 72
        assert d["retention_hours_paper"] == 24
        assert d["purge_batch_cap"] == 5000
        assert isinstance(d["total_backlog"], int)
        assert isinstance(d["more_remains"], bool)
        assert isinstance(d["breakdown"], list)
        assert isinstance(d["preserved_collections"], list)
        assert isinstance(d["total_purged_lifetime"], int)

    def test_breakdown_covers_nine_rules(self, admin_session):
        d = admin_session.get(
            f"{BASE_URL}/api/admin/retention/status", timeout=30,
        ).json()
        collections = {row["collection"] for row in d["breakdown"]}
        expected = {
            "predictions",
            "day_trade_targets",
            "paper_trades",
            "mc2_heartbeats",
            "mc2_contributions",
            "mc2_stances",
            "mc2_intents",
            "signal_dispatcher_events",
            "alert_events",
        }
        assert expected.issubset(collections), (
            f"missing collections in breakdown: {expected - collections}"
        )
        assert len(d["breakdown"]) == 9, (
            f"expected 9 rules, got {len(d['breakdown'])}: {collections}"
        )

    def test_preserved_collections_list(self, admin_session):
        d = admin_session.get(
            f"{BASE_URL}/api/admin/retention/status", timeout=30,
        ).json()
        expected = {
            "equity_live_trades",
            "trade_orders",
            "broker_connections",
            "users",
            "watchlists",
            "portfolio_snapshots",
            "portfolio_history",
        }
        assert expected.issubset(set(d["preserved_collections"]))

    def test_paper_trades_row_flags_preserves_active(self, admin_session):
        d = admin_session.get(
            f"{BASE_URL}/api/admin/retention/status", timeout=30,
        ).json()
        rows = {row["collection"]: row for row in d["breakdown"]}
        assert rows["paper_trades"]["preserves_active"] is True
        assert rows["paper_trades"]["ttl_hours"] == 24
        assert rows["predictions"]["ttl_hours"] == 72


# ── (b) 403 for non-admin ────────────────────────────────────────


class TestRetentionAuthz:
    def test_status_rejects_non_admin(self, non_admin_session):
        r = non_admin_session.get(
            f"{BASE_URL}/api/admin/retention/status", timeout=30,
        )
        assert r.status_code == 403, (
            f"expected 403 for non-admin, got {r.status_code} {r.text[:200]}"
        )

    def test_purge_rejects_non_admin(self, non_admin_session):
        r = non_admin_session.post(
            f"{BASE_URL}/api/admin/retention/purge", timeout=30,
        )
        assert r.status_code == 403, (
            f"expected 403 for non-admin, got {r.status_code} {r.text[:200]}"
        )

    def test_status_rejects_anonymous(self):
        r = requests.get(f"{BASE_URL}/api/admin/retention/status", timeout=30)
        assert r.status_code in (401, 403), r.status_code


# ── (c) POST /purge — shape, idempotence, batch cap ──────────────


class TestPurgeSemantics:
    def test_purge_response_shape(self, admin_session):
        r = admin_session.post(
            f"{BASE_URL}/api/admin/retention/purge", timeout=60,
        )
        assert r.status_code == 200, r.text
        d = r.json()
        for k in (
            "purged_total",
            "more_remains",
            "batch_cap",
            "duration_ms",
            "per_collection",
            "triggered_by",
            "started_at",
            "finished_at",
        ):
            assert k in d, f"missing key: {k}"
        assert d["triggered_by"] == "manual"
        assert d["batch_cap"] == 5000
        assert isinstance(d["per_collection"], list)
        assert d["purged_total"] <= 5000
        assert isinstance(d["duration_ms"], int)
        assert d["duration_ms"] >= 0

    def test_purge_is_idempotent_drain_to_empty(self, admin_session):
        max_iters = 20
        d = {}
        for _ in range(max_iters):
            r = admin_session.post(
                f"{BASE_URL}/api/admin/retention/purge", timeout=60,
            )
            assert r.status_code == 200
            d = r.json()
            if not d["more_remains"]:
                break
        else:
            pytest.fail(
                f"backlog did not drain after {max_iters} purges; last={d}",
            )
        r2 = admin_session.post(
            f"{BASE_URL}/api/admin/retention/purge", timeout=60,
        )
        assert r2.status_code == 200
        d2 = r2.json()
        assert d2["purged_total"] == 0, (
            f"expected 0 after drain, got {d2['purged_total']}"
        )
        assert d2["more_remains"] is False


# ── (d) audit log written; lifetime counter increments ──────────


class TestPurgeAudit:
    def test_audit_row_written_and_lifetime_increments(self, admin_session):
        old = datetime.now(timezone.utc) - timedelta(hours=200)
        marker = "TEST_retention_" + uuid.uuid4().hex[:8]

        async def _seed(db):
            await db.predictions.insert_one({
                "_test_marker": marker,
                "ticker": marker,
                "timestamp": old.isoformat(),
                "payload": "synthetic",
            })

        _run_db(_seed)

        pre = admin_session.get(
            f"{BASE_URL}/api/admin/retention/status", timeout=30,
        ).json()
        pre_lifetime = pre["total_purged_lifetime"]

        r = admin_session.post(
            f"{BASE_URL}/api/admin/retention/purge", timeout=60,
        )
        assert r.status_code == 200
        body = r.json()

        async def _tail(db):
            log = await db.retention_purge_log.find_one(
                sort=[("finished_at", -1)],
                projection={"_id": 0},
            )
            still_there = await db.predictions.find_one(
                {"_test_marker": marker},
            )
            await db.predictions.delete_many({"_test_marker": marker})
            return log, still_there

        log, still_there = _run_db(_tail)

        assert log is not None, "no retention_purge_log audit row present"
        assert log["triggered_by"] == "manual"
        for k in (
            "purged_total", "more_remains", "per_collection",
            "started_at", "finished_at", "duration_ms",
        ):
            assert k in log, f"missing audit field {k}"
        assert log["purged_total"] == body["purged_total"]

        assert still_there is None, (
            "synthetic old predictions row survived the purge"
        )

        post = admin_session.get(
            f"{BASE_URL}/api/admin/retention/status", timeout=30,
        ).json()
        assert (
            post["total_purged_lifetime"] >= pre_lifetime + body["purged_total"]
        ), (
            f"lifetime did not increment: pre={pre_lifetime}, "
            f"post={post['total_purged_lifetime']}, "
            f"purged={body['purged_total']}"
        )


# ── (e) PRESERVED collections survive purge ─────────────────────


class TestPreservedCollectionsSurvive:
    def test_equity_live_trade_row_survives(self, admin_session):
        old = datetime.now(timezone.utc) - timedelta(days=400)
        marker = "TEST_preserve_" + uuid.uuid4().hex[:8]

        async def _seed(db):
            await db.equity_live_trades.insert_one({
                "_test_marker": marker,
                "symbol": "TESTX",
                "opened_at": old,
                "opened_at_iso": old.isoformat(),
                "timestamp": old.isoformat(),
                "created_at": old.isoformat(),
                "status": "closed",
            })

        _run_db(_seed)

        try:
            for _ in range(3):
                r = admin_session.post(
                    f"{BASE_URL}/api/admin/retention/purge", timeout=60,
                )
                assert r.status_code == 200
                if not r.json()["more_remains"]:
                    break

            async def _verify(db):
                doc = await db.equity_live_trades.find_one(
                    {"_test_marker": marker},
                )
                return doc

            doc = _run_db(_verify)
            assert doc is not None, (
                "PRESERVED equity_live_trades row was deleted by purge!"
            )
        finally:
            async def _cleanup(db):
                await db.equity_live_trades.delete_many(
                    {"_test_marker": marker},
                )
            _run_db(_cleanup)


# ── (f) Paper-trade preservation of open/active/pending ─────────


class TestPaperTradePreservation:
    @pytest.mark.parametrize("status", ["open", "active", "pending"])
    def test_open_paper_trade_not_purged(self, admin_session, status):
        old = datetime.now(timezone.utc) - timedelta(hours=90)
        marker = f"TEST_paper_{status}_" + uuid.uuid4().hex[:8]

        async def _seed(db):
            await db.paper_trades.insert_one({
                "_test_marker": marker,
                "symbol": "PAPERX",
                "ticker": "PAPERX",
                "status": status,
                "opened_at": old,
                "opened_at_iso": old.isoformat(),
            })

        _run_db(_seed)

        try:
            for _ in range(3):
                r = admin_session.post(
                    f"{BASE_URL}/api/admin/retention/purge", timeout=60,
                )
                assert r.status_code == 200
                if not r.json()["more_remains"]:
                    break

            async def _check(db):
                return await db.paper_trades.find_one(
                    {"_test_marker": marker},
                )

            doc = _run_db(_check)
            assert doc is not None, (
                f"paper_trades row with status={status!r} was "
                f"incorrectly purged despite the preserve filter"
            )
        finally:
            async def _cleanup(db):
                await db.paper_trades.delete_many({"_test_marker": marker})
            _run_db(_cleanup)

    def test_closed_paper_trade_is_purged(self, admin_session):
        """Sanity: a *closed* paper trade older than 24h SHOULD go."""
        old = datetime.now(timezone.utc) - timedelta(hours=90)
        marker = "TEST_paper_closed_" + uuid.uuid4().hex[:8]

        async def _seed(db):
            await db.paper_trades.insert_one({
                "_test_marker": marker,
                "symbol": "PAPERC",
                "status": "closed",
                "opened_at": old,
            })

        _run_db(_seed)

        try:
            for _ in range(3):
                r = admin_session.post(
                    f"{BASE_URL}/api/admin/retention/purge", timeout=60,
                )
                assert r.status_code == 200
                if not r.json()["more_remains"]:
                    break

            async def _check(db):
                return await db.paper_trades.find_one(
                    {"_test_marker": marker},
                )

            doc = _run_db(_check)
            assert doc is None, (
                "closed paper_trades row older than 24h should have "
                "been purged but survived"
            )
        finally:
            async def _cleanup(db):
                await db.paper_trades.delete_many({"_test_marker": marker})
            _run_db(_cleanup)


# ── (g) Regressions from iteration 180 & 181 ────────────────────


class TestRegressions:
    def test_broker_positions_endpoint_reachable(self, admin_session):
        r = admin_session.get(
            f"{BASE_URL}/api/broker/positions/public", timeout=30,
        )
        assert r.status_code in (200, 404), r.status_code
        if r.status_code == 200:
            symbols = [p["symbol"] for p in r.json().get("positions", [])]
            assert symbols == sorted(symbols), (
                f"positions not sorted: {symbols}"
            )

    def test_watchlist_still_merges(self, admin_session):
        r = admin_session.get(
            f"{BASE_URL}/api/workspace/watchlist", timeout=30,
        )
        assert r.status_code == 200
        body = r.json()
        assert "tickers" in body
        assert isinstance(body["tickers"], list)

    def test_war_room_under_15s(self, admin_session):
        """POST /api/web-intel/war-room must respond within 15 s
        (regression from iteration 180)."""
        t0 = time.time()
        r = admin_session.post(
            f"{BASE_URL}/api/web-intel/war-room",
            json={"query": "NVDA earnings", "symbol": "NVDA", "mode": "auto"},
            timeout=30,
        )
        elapsed = time.time() - t0
        # 402 acceptable if credits are exhausted; 200 is the happy path
        assert r.status_code in (200, 402), (
            f"war-room: HTTP={r.status_code} body={r.text[:200]}"
        )
        assert elapsed < 15.0, (
            f"war-room took {elapsed:.1f}s (regression: iter-180 cap = 15s)"
        )
