"""Broker comparison service — aggregation math + composite scoring."""
from __future__ import annotations

import os
import sqlite3
import time
from unittest import mock

import pytest


@pytest.fixture()
def temp_hot_store(tmp_path, monkeypatch):
    db = tmp_path / "hot.sqlite"
    monkeypatch.setenv("ALPHA_HOT_STORE_PATH", str(db))
    # Reset the module-level path cache in alpha_hot_store.
    from services import alpha_hot_store
    monkeypatch.setattr(alpha_hot_store, "_DB_PATH", None)
    alpha_hot_store.init(str(db))
    # Create the comparison table so inserts work.
    with sqlite3.connect(str(db)) as con:
        con.execute("""CREATE TABLE IF NOT EXISTS broker_comparison (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            broker TEXT NOT NULL,
            client_order_id TEXT,
            broker_order_id TEXT,
            symbol TEXT,
            side TEXT,
            qty REAL,
            limit_price REAL,
            submit_latency_ms INTEGER,
            ack_latency_ms INTEGER,
            fill_latency_ms INTEGER,
            fill_price REAL,
            slippage_bps REAL,
            status TEXT,
            error TEXT,
            extra TEXT,
            ts_ns INTEGER NOT NULL
        );""")
    yield str(db)


def _insert(path, **fields):
    with sqlite3.connect(path) as con:
        con.execute(
            """INSERT INTO broker_comparison(
                broker, client_order_id, broker_order_id, symbol, side,
                qty, limit_price, submit_latency_ms, ack_latency_ms,
                fill_latency_ms, fill_price, slippage_bps, status, error, extra, ts_ns
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                fields.get("broker", "public"),
                fields.get("client_order_id", "c1"),
                fields.get("broker_order_id", "o1"),
                fields.get("symbol", "AAPL"),
                fields.get("side", "BUY"),
                fields.get("qty", 1.0),
                fields.get("limit_price", 100.0),
                fields.get("submit_latency_ms", 0),
                fields.get("ack_latency_ms", 100),
                fields.get("fill_latency_ms", 200),
                fields.get("fill_price", 100.0),
                fields.get("slippage_bps", 0.0),
                fields.get("status", "filled"),
                fields.get("error", None),
                "{}",
                fields.get("ts_ns", time.time_ns()),
            ),
        )


def test_empty_store_returns_zero_samples_and_no_winner(temp_hot_store):
    from services.broker_comparison_service import compare
    r = compare(window="1d")
    assert r["brokers"]["public"]["samples"] == 0
    assert r["brokers"]["moomoo"]["samples"] == 0
    assert r["winner"] is None
    assert r["low_sample_warning"] is True
    # Weights are exposed and add to 1.0
    w = r["weights"]
    assert abs(w["slippage"] + w["fill_rate"] + w["ack_latency"] - 1.0) < 1e-6


def test_public_wins_on_slippage_when_moomoo_is_slippier(temp_hot_store):
    from services.broker_comparison_service import compare
    # Public: low slippage, low ack
    for _ in range(15):
        _insert(temp_hot_store, broker="public", slippage_bps=1.5, ack_latency_ms=80)
    # MooMoo: much higher slippage, similar ack
    for _ in range(15):
        _insert(temp_hot_store, broker="moomoo", slippage_bps=6.0, ack_latency_ms=85)
    r = compare(window="1d")
    assert r["winner"] == "public"
    assert r["brokers"]["public"]["composite_score"] > r["brokers"]["moomoo"]["composite_score"]
    # Slippage weighting dominates the outcome
    assert r["weights"]["slippage"] == 0.50


def test_moomoo_wins_when_slippage_and_latency_both_better(temp_hot_store):
    from services.broker_comparison_service import compare
    for _ in range(15):
        _insert(temp_hot_store, broker="public", slippage_bps=8.0, ack_latency_ms=250)
    for _ in range(15):
        _insert(temp_hot_store, broker="moomoo", slippage_bps=1.5, ack_latency_ms=95)
    r = compare(window="1d")
    assert r["winner"] == "moomoo"


def test_low_sample_warning_flag(temp_hot_store):
    from services.broker_comparison_service import compare, LOW_SAMPLE_THRESHOLD
    assert LOW_SAMPLE_THRESHOLD == 10
    for _ in range(3):
        _insert(temp_hot_store, broker="public", slippage_bps=1.0, ack_latency_ms=50)
    r = compare(window="1d")
    assert r["low_sample_warning"] is True
    assert r["brokers"]["public"]["samples"] == 3


def test_fill_rate_reflects_rejections(temp_hot_store):
    from services.broker_comparison_service import compare
    for _ in range(8):
        _insert(temp_hot_store, broker="public", slippage_bps=1.0, ack_latency_ms=50)
    for _ in range(2):
        _insert(temp_hot_store, broker="public", slippage_bps=None,
                fill_price=None, error="rejected", status="rejected")
    r = compare(window="1d")
    pub = r["brokers"]["public"]
    assert pub["samples"] == 10
    assert pub["filled"] == 8
    assert 0.79 < pub["fill_rate"] < 0.81


def test_window_filters_old_rows(temp_hot_store):
    from services.broker_comparison_service import compare
    old_ts = time.time_ns() - int(2 * 86_400 * 1e9)  # 2 days ago
    for _ in range(5):
        _insert(temp_hot_store, broker="public", slippage_bps=1.0, ts_ns=old_ts)
    for _ in range(5):
        _insert(temp_hot_store, broker="public", slippage_bps=5.0, ts_ns=time.time_ns())
    r_1d = compare(window="1d")
    r_7d = compare(window="7d")
    assert r_1d["brokers"]["public"]["samples"] == 5
    assert r_7d["brokers"]["public"]["samples"] == 10


def test_recent_rows_returns_most_recent_first(temp_hot_store):
    from services.broker_comparison_service import recent_rows
    base = time.time_ns()
    _insert(temp_hot_store, broker="public", symbol="OLD", ts_ns=base - 1_000_000_000)
    _insert(temp_hot_store, broker="moomoo", symbol="NEW", ts_ns=base)
    rows = recent_rows(limit=10)
    assert rows[0]["symbol"] == "NEW"
    assert rows[1]["symbol"] == "OLD"


def test_symbol_filter(temp_hot_store):
    from services.broker_comparison_service import compare
    for _ in range(6):
        _insert(temp_hot_store, broker="public", symbol="AAPL", slippage_bps=1.0)
    for _ in range(6):
        _insert(temp_hot_store, broker="public", symbol="MSFT", slippage_bps=8.0)
    r = compare(window="1d", symbol="AAPL")
    assert r["brokers"]["public"]["samples"] == 6
    assert r["brokers"]["public"]["avg_slippage_bps"] == pytest.approx(1.0)


def test_record_public_submit_writes_row(temp_hot_store):
    from services.broker_comparison_service import record_public_submit, recent_rows
    record_public_submit(
        client_order_id="c-1", broker_order_id="ord-1",
        symbol="AAPL", side="BUY", qty=0.5, limit_price=200.0,
        submit_latency_ms=120, ack_latency_ms=120,
        fill_price=200.20, status="filled", error=None,
    )
    rows = recent_rows(limit=1)
    assert len(rows) == 1
    row = rows[0]
    assert row["broker"] == "public"
    assert row["symbol"] == "AAPL"
    # 20 bps slippage on a BUY at $200 filling at $200.20
    assert row["slippage_bps"] == pytest.approx(10.0, rel=0.01)


def test_record_public_submit_sell_slippage_sign(temp_hot_store):
    from services.broker_comparison_service import record_public_submit, recent_rows
    # SELL at $200 expected, filled at $199.80 → 10 bps bad slippage
    record_public_submit(
        client_order_id="c-sell", broker_order_id="ord-sell",
        symbol="AAPL", side="SELL", qty=0.5, limit_price=200.0,
        submit_latency_ms=90, ack_latency_ms=90,
        fill_price=199.80, status="filled",
    )
    rows = recent_rows(limit=1)
    assert rows[0]["slippage_bps"] == pytest.approx(10.0, rel=0.01)


def test_only_one_broker_has_data(temp_hot_store):
    """MooMoo has samples, Public has none → MooMoo should win by default."""
    from services.broker_comparison_service import compare
    for _ in range(12):
        _insert(temp_hot_store, broker="moomoo", slippage_bps=2.0, ack_latency_ms=100)
    r = compare(window="1d")
    assert r["winner"] == "moomoo"
    assert r["brokers"]["public"]["samples"] == 0
