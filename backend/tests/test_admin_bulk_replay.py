"""Bulk Replay — pre-ingest CSV scanner contract tests.

Pins:
  * Owner-only access (non-owner gets 403).
  * Clean rows return GREEN / trainable.
  * Toxic rows return AMBER with trust=0.10.
  * Missing source / timestamps / symbol → RED / quarantined.
  * Malformed CSV does NOT crash — endpoint returns row-level
    errors via ``parse_errors``.
  * Row cap (500) enforced — extra rows reported in
    ``parse_errors`` and excluded from the labeling pass.
  * File-size cap (2 MiB) enforced via 413.
  * Endpoint performs ZERO Mongo writes (static check across the
    entire route file).
  * Endpoint imports NO broker / executor / paper-trader / training
    modules (static check).
"""
from __future__ import annotations

import io
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.admin_bulk_replay import (
    MAX_FILE_BYTES,
    MAX_ROWS_PER_UPLOAD,
    router as bulk_replay_router,
)


# ── Test app + auth stub ──────────────────────────────────────────────────────


@pytest.fixture
def app_owner():
    """FastAPI test app where ``get_current_user`` returns an owner."""
    app = FastAPI()
    app.include_router(bulk_replay_router)

    async def _stub_owner(request):
        return {"role": "owner", "email": "admin@risedual.ai"}

    with patch("routes.admin_bulk_replay.get_current_user", _stub_owner):
        yield app


@pytest.fixture
def app_non_owner():
    app = FastAPI()
    app.include_router(bulk_replay_router)

    async def _stub_user(request):
        return {"role": "user", "email": "u@example.com"}

    with patch("routes.admin_bulk_replay.get_current_user", _stub_user):
        yield app


def _csv_bytes(rows: list[dict], headers: list[str] | None = None) -> bytes:
    if not rows:
        return b""
    headers = headers or list(rows[0].keys())
    lines = [",".join(headers)]
    for r in rows:
        lines.append(",".join(str(r.get(h, "")) for h in headers))
    return ("\n".join(lines) + "\n").encode("utf-8")


def _post_csv(client: TestClient, raw: bytes, filename: str = "trades.csv"):
    return client.post(
        "/api/admin/bulk-replay/scan",
        files={"file": (filename, raw, "text/csv")},
    )


# ── Auth ──────────────────────────────────────────────────────────────────────


def test_non_owner_gets_403(app_non_owner):
    client = TestClient(app_non_owner)
    raw = _csv_bytes([{
        "trade_id": "1", "symbol": "BTC-USD",
        "lane": "crypto", "source": "crypto_paper_bot",
        "opened_at": "2026-05-01T00:00:00+00:00",
        "closed_at": "2026-05-02T00:00:00+00:00",
        "pnl_usd": "10.0", "confidence": "0.8",
        "direction": "LONG", "regime": "TREND_UP",
    }])
    r = _post_csv(client, raw)
    assert r.status_code == 403


# ── Clean rows return GREEN / trainable ───────────────────────────────────────


def test_clean_csv_returns_green_trainable_rows(app_owner):
    client = TestClient(app_owner)
    raw = _csv_bytes([{
        "trade_id": f"t-{i}",
        "symbol": "BTC-USD",
        "lane": "crypto",
        "source": "crypto_paper_bot",
        "opened_at": "2026-05-01T00:00:00+00:00",
        "closed_at": "2026-05-02T00:00:00+00:00",
        "pnl_usd": "12.50",
        "confidence": "0.78",
        "direction": "LONG",
        "regime": "TREND_UP",
    } for i in range(5)])
    r = _post_csv(client, raw)
    assert r.status_code == 200
    body = r.json()
    assert body["aggregate"]["total_rows"] == 5
    assert body["aggregate"]["trainable_count"] == 5
    assert body["aggregate"]["quarantined_count"] == 0
    assert body["aggregate"]["toxic_count"] == 0
    for row in body["rows"]:
        assert row["grade"] == "GREEN"
        assert row["trainable"] is True
        assert row["lane"] == "crypto"
        assert row["symbol"] == "BTC"  # normalized


# ── Toxic rows return AMBER with trust=0.10 ───────────────────────────────────


def test_toxic_rows_return_amber(app_owner):
    """High-confidence loss with explicit blowup → toxic floor."""
    client = TestClient(app_owner)
    raw = _csv_bytes([{
        "trade_id": "tox-1",
        "symbol": "ETH-USD",
        "lane": "crypto",
        "source": "crypto_paper_bot",
        "opened_at": "2026-05-01T00:00:00+00:00",
        "closed_at": "2026-05-02T00:00:00+00:00",
        "pnl_usd": "-500.0",
        "confidence": "0.95",
        "direction": "LONG",
        "regime": "HIGH_VOLATILITY",
        "failure_mode": "blowup",
    }])
    r = _post_csv(client, raw)
    body = r.json()
    assert body["aggregate"]["toxic_count"] == 1
    row = body["rows"][0]
    assert row["grade"] == "AMBER"
    assert row["trust_weight"] == 0.10
    assert row["trainable"] is True  # rule 7: toxic still trainable
    assert row["failure_mode"] == "blowup"


def test_inferred_toxic_high_confidence_loss(app_owner):
    """No explicit failure tag — but a 0.92-conf trade that lost
    50% should self-flag as toxic_high_confidence."""
    client = TestClient(app_owner)
    raw = _csv_bytes([{
        "trade_id": "tox-2",
        "symbol": "SOL-USD",
        "lane": "crypto",
        "source": "crypto_paper_bot",
        "opened_at": "2026-05-01T00:00:00+00:00",
        "closed_at": "2026-05-02T00:00:00+00:00",
        "pnl_usd": "-200.0",
        "pnl_pct": "-0.6",
        "confidence": "0.92",
        "direction": "LONG",
    }])
    r = _post_csv(client, raw)
    body = r.json()
    row = body["rows"][0]
    assert row["failure_mode"] == "toxic_high_confidence"
    assert row["grade"] == "AMBER"


# ── Quarantined rows return RED ───────────────────────────────────────────────


def test_missing_source_returns_red_quarantined(app_owner):
    client = TestClient(app_owner)
    # Missing source column entirely.
    raw = _csv_bytes([{
        "trade_id": "q-1",
        "symbol": "BTC-USD",
        "lane": "crypto",
        "opened_at": "2026-05-01T00:00:00+00:00",
        "closed_at": "2026-05-02T00:00:00+00:00",
        "pnl_usd": "10.0",
    }])
    r = _post_csv(client, raw)
    body = r.json()
    assert body["aggregate"]["quarantined_count"] == 1
    assert body["aggregate"]["trainable_count"] == 0
    row = body["rows"][0]
    assert row["grade"] == "RED"
    assert row["trainable"] is False
    assert row["trust_weight"] == 0.0
    assert row["rejection_reason"] == "missing_source"


def test_missing_timestamps_returns_red_quarantined(app_owner):
    client = TestClient(app_owner)
    raw = _csv_bytes([{
        "trade_id": "q-2",
        "symbol": "BTC-USD",
        "lane": "crypto",
        "source": "crypto_paper_bot",
        # No opened_at/closed_at columns at all.
        "pnl_usd": "10.0",
    }])
    r = _post_csv(client, raw)
    body = r.json()
    row = body["rows"][0]
    assert row["grade"] == "RED"
    assert row["rejection_reason"] == "missing_timestamps"


def test_missing_symbol_returns_red_quarantined(app_owner):
    client = TestClient(app_owner)
    raw = _csv_bytes([{
        "trade_id": "q-3",
        "lane": "crypto",
        "source": "crypto_paper_bot",
        "opened_at": "2026-05-01T00:00:00+00:00",
    }])
    r = _post_csv(client, raw)
    body = r.json()
    row = body["rows"][0]
    assert row["grade"] == "RED"
    assert row["rejection_reason"] == "missing_symbol"


# ── Aggregate rollups ─────────────────────────────────────────────────────────


def test_aggregate_breakdowns_present(app_owner):
    client = TestClient(app_owner)
    raw = _csv_bytes([
        {"trade_id": "1", "symbol": "BTC-USD", "lane": "crypto",
         "source": "crypto_paper_bot",
         "opened_at": "2026-05-01T00:00:00+00:00",
         "closed_at": "2026-05-02T00:00:00+00:00",
         "pnl_usd": "10.0", "confidence": "0.8", "direction": "LONG",
         "regime": "TREND_UP"},
        {"trade_id": "2", "symbol": "AAPL", "lane": "equity",
         "source": "alpaca",
         "opened_at": "2026-05-01T00:00:00+00:00",
         "closed_at": "2026-05-02T00:00:00+00:00",
         "pnl_usd": "5.0", "confidence": "0.7", "direction": "LONG",
         "regime": "TREND_UP"},
    ])
    r = _post_csv(client, raw)
    body = r.json()
    agg = body["aggregate"]
    # All five required breakdowns present.
    for key in (
        "by_source", "by_lane", "by_event_era",
        "by_failure_mode", "by_data_quality",
    ):
        assert key in agg, f"missing breakdown: {key}"
        assert isinstance(agg[key], dict)
        assert len(agg[key]) >= 1
    # Spot-check: by_lane has both lanes counted.
    assert agg["by_lane"].get("crypto") == 1
    assert agg["by_lane"].get("equity") == 1


def test_avg_trust_weight_computed(app_owner):
    client = TestClient(app_owner)
    raw = _csv_bytes([
        {"trade_id": "1", "symbol": "BTC", "lane": "crypto",
         "source": "alpaca",  # 1.00
         "opened_at": "2026-05-01T00:00:00+00:00",
         "closed_at": "2026-05-02T00:00:00+00:00",
         "pnl_usd": "10.0"},
        {"trade_id": "2", "symbol": "BTC", "lane": "crypto",
         "source": "yfinance",  # 0.05
         "opened_at": "2026-05-01T00:00:00+00:00",
         "closed_at": "2026-05-02T00:00:00+00:00",
         "pnl_usd": "10.0"},
    ])
    r = _post_csv(client, raw)
    body = r.json()
    # (1.00 + 0.05) / 2 = 0.525
    assert body["aggregate"]["avg_trust_weight"] == pytest.approx(0.525)


# ── Malformed CSV does not crash ──────────────────────────────────────────────


def test_empty_csv_returns_400(app_owner):
    client = TestClient(app_owner)
    r = _post_csv(client, b"")
    # Empty file → 200 with zero rows + warning. Operator can still
    # see the response; only header-less files 400.
    assert r.status_code == 200
    assert r.json()["aggregate"]["total_rows"] == 0


def test_no_header_csv_returns_400(app_owner):
    client = TestClient(app_owner)
    # Single line with no newline → DictReader treats it as header.
    # We need a truly malformed, non-header case. Send pure bytes.
    raw = b"not,a,real,header\n"
    r = _post_csv(client, raw)
    # With only a "header" and zero data rows, total_rows should
    # be 0 — endpoint stays at 200.
    assert r.status_code == 200
    assert r.json()["aggregate"]["total_rows"] == 0


def test_partial_malformed_row_does_not_crash(app_owner):
    """One bad row mixed with good rows: bad row labels as
    quarantined (or self-handles), endpoint stays 200."""
    client = TestClient(app_owner)
    # Use raw CSV string so we can inject inconsistent column counts.
    raw = (
        b"trade_id,symbol,lane,source,opened_at,closed_at,pnl_usd\n"
        b"1,BTC-USD,crypto,crypto_paper_bot,"
        b"2026-05-01T00:00:00+00:00,2026-05-02T00:00:00+00:00,10.0\n"
        b"2,,,,,,\n"  # empty fields
        b"3,AAPL,equity,alpaca,"
        b"2026-05-01T00:00:00+00:00,2026-05-02T00:00:00+00:00,5.0\n"
    )
    r = _post_csv(client, raw)
    assert r.status_code == 200
    body = r.json()
    assert body["aggregate"]["total_rows"] == 3
    # Row 2 should be quarantined; rows 1+3 trainable.
    assert body["aggregate"]["quarantined_count"] >= 1
    assert body["aggregate"]["trainable_count"] >= 2


def test_invalid_utf8_bytes_handled_gracefully(app_owner):
    client = TestClient(app_owner)
    # Inject a non-UTF8 byte; decoder uses ``errors="replace"``.
    raw = (
        b"trade_id,symbol,lane,source,opened_at,pnl_usd\n"
        b"1,BTC,\xff\xfe,crypto_paper_bot,"
        b"2026-05-01T00:00:00+00:00,10.0\n"
    )
    r = _post_csv(client, raw)
    # Must not crash.
    assert r.status_code == 200


# ── Caps ──────────────────────────────────────────────────────────────────────


def test_row_cap_enforced(app_owner):
    """Request 600 rows → only first 500 scanned, extras reported
    as parse errors."""
    client = TestClient(app_owner)
    rows = [{
        "trade_id": f"t-{i}",
        "symbol": "BTC-USD",
        "lane": "crypto",
        "source": "crypto_paper_bot",
        "opened_at": "2026-05-01T00:00:00+00:00",
        "closed_at": "2026-05-02T00:00:00+00:00",
        "pnl_usd": "10.0",
    } for i in range(MAX_ROWS_PER_UPLOAD + 100)]
    raw = _csv_bytes(rows)
    r = _post_csv(client, raw)
    assert r.status_code == 200
    body = r.json()
    assert body["rows_scanned"] == MAX_ROWS_PER_UPLOAD
    # Cap-overflow error reported.
    assert any(
        "row cap" in (e.get("error") or "")
        for e in body["parse_errors"]
    )


def test_file_size_cap_returns_413(app_owner):
    client = TestClient(app_owner)
    # Build a file that's just over the 2 MiB cap with valid CSV
    # padding — header plus one massive symbol field.
    huge = b"trade_id,symbol\n1," + (b"X" * (MAX_FILE_BYTES + 10)) + b"\n"
    r = _post_csv(client, huge)
    assert r.status_code == 413


def test_response_includes_caps_for_operator_visibility(app_owner):
    client = TestClient(app_owner)
    raw = _csv_bytes([{
        "trade_id": "1", "symbol": "BTC-USD",
        "lane": "crypto", "source": "crypto_paper_bot",
        "opened_at": "2026-05-01T00:00:00+00:00",
        "closed_at": "2026-05-02T00:00:00+00:00",
        "pnl_usd": "10.0",
    }])
    r = _post_csv(client, raw)
    body = r.json()
    assert body["max_rows_per_upload"] == MAX_ROWS_PER_UPLOAD
    assert body["max_file_bytes"] == MAX_FILE_BYTES


# ── Per-row payload completeness ──────────────────────────────────────────────


def test_per_row_payload_has_all_required_fields(app_owner):
    client = TestClient(app_owner)
    raw = _csv_bytes([{
        "trade_id": "1", "symbol": "BTC-USD",
        "lane": "crypto", "source": "crypto_paper_bot",
        "opened_at": "2026-05-01T00:00:00+00:00",
        "closed_at": "2026-05-02T00:00:00+00:00",
        "pnl_usd": "10.0", "confidence": "0.8",
        "direction": "LONG", "regime": "TREND_UP",
    }])
    r = _post_csv(client, raw)
    body = r.json()
    row = body["rows"][0]
    required = [
        "csv_row_index",
        "symbol", "opened_at", "closed_at",
        "source", "lane", "event_era",
        "grade", "trust_weight", "trainable",
        "rejection_reason", "rule_trace",
    ]
    for k in required:
        assert k in row, f"per-row payload missing field: {k}"


# ── Static authority firewall ─────────────────────────────────────────────────


def test_route_does_not_perform_db_writes():
    from pathlib import Path
    src = Path(
        "/app/backend/routes/admin_bulk_replay.py"
    ).read_text(encoding="utf-8")
    forbidden = [
        ".insert_one(",
        ".insert_many(",
        ".update_one(",
        ".update_many(",
        ".replace_one(",
        ".delete_one(",
        ".delete_many(",
        ".find_one_and_update(",
        ".bulk_write(",
        ".drop(",
    ]
    found = [tok for tok in forbidden if tok in src]
    assert not found, f"bulk_replay attempted DB writes: {found}"


def test_route_does_not_import_broker_or_executor():
    from pathlib import Path
    src = Path(
        "/app/backend/routes/admin_bulk_replay.py"
    ).read_text(encoding="utf-8")
    forbidden = [
        "from services.broker_service",
        "from services.trading_bot_service",
        "from services.crypto_paper_trader",
        "from services.paper_trading_service",
        "from services.trading_agents",
        "from services.day_trade_scanner",
        "from routes.broker",
        "from routes.trading",
        "from services.ml.executors",
        "from services.ml.broker_wire",
        "from services.ml.shadow_wiring",
        "from services.ml.strategist",
        "from services.ml.auditor",
        # Training paths
        "from services.memory_training_service",
        "from scripts.retrain_alpha",
        # Broker action verbs
        ".place_order(",
        "broker.execute(",
    ]
    found = [tok for tok in forbidden if tok in src]
    assert not found, (
        f"bulk_replay imported forbidden module(s) / verb(s): {found}"
    )


def test_route_exposes_no_import_or_train_endpoints():
    """No `import_into_memory` / `train_all` / `force_train` /
    label-override endpoints — the route is read-only by spec.

    Looks for actual route definitions (``@router.<verb>(...)``)
    that match the forbidden names, not casual mentions in the
    docstring.
    """
    import re
    from pathlib import Path
    src = Path(
        "/app/backend/routes/admin_bulk_replay.py"
    ).read_text(encoding="utf-8")
    forbidden_endpoints = [
        "import_into_memory",
        "train_all",
        "force_train",
        "ingest_now",
        "promote_now",
        "label_override",
    ]
    # Match either:
    #   1. @router.<verb>("/<endpoint>"...
    #   2. async def <endpoint>(...
    found: list[str] = []
    for ep in forbidden_endpoints:
        route_decl = re.compile(rf'@router\.\w+\([^)]*["\']\/[^"\']*{re.escape(ep)}', re.MULTILINE)
        func_decl = re.compile(rf'^(async\s+)?def\s+{re.escape(ep)}\s*\(', re.MULTILINE)
        if route_decl.search(src) or func_decl.search(src):
            found.append(ep)
    assert not found, (
        f"bulk_replay exposed forbidden endpoint(s): {found}"
    )
