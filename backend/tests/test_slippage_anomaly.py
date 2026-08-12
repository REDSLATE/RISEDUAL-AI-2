"""Slippage anomaly detector — baseline math + threshold triggering."""
from __future__ import annotations

import sqlite3
import time

import pytest


@pytest.fixture()
def temp_hot_store(tmp_path, monkeypatch):
    db = tmp_path / "hot.sqlite"
    monkeypatch.setenv("ALPHA_HOT_STORE_PATH", str(db))
    from services import alpha_hot_store
    monkeypatch.setattr(alpha_hot_store, "_DB_PATH", None)
    alpha_hot_store.init(str(db))
    with sqlite3.connect(str(db)) as con:
        con.execute("""CREATE TABLE IF NOT EXISTS broker_comparison (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            broker TEXT NOT NULL,
            client_order_id TEXT, broker_order_id TEXT,
            symbol TEXT, side TEXT, qty REAL,
            limit_price REAL, submit_latency_ms INTEGER,
            ack_latency_ms INTEGER, fill_latency_ms INTEGER,
            fill_price REAL, slippage_bps REAL,
            status TEXT, error TEXT, extra TEXT,
            ts_ns INTEGER NOT NULL
        );""")
    yield str(db)


def _seed(path, broker, slippage, ts_ns):
    with sqlite3.connect(path) as con:
        con.execute(
            "INSERT INTO broker_comparison(broker, slippage_bps, ts_ns, status) VALUES (?, ?, ?, ?)",
            (broker, float(slippage), int(ts_ns), "filled"),
        )


def test_insufficient_recent_samples(temp_hot_store):
    from services.slippage_anomaly import evaluate_broker
    now = time.time_ns()
    # Baseline only, no recent.
    for i in range(30):
        _seed(temp_hot_store, "public", 1.0 + (i * 0.01), now - int(2 * 86_400 * 1e9))
    r = evaluate_broker("public", now_ns=now)
    assert r["triggered"] is False
    assert "insufficient_recent_samples" in r["reason"]


def test_insufficient_baseline_samples(temp_hot_store):
    from services.slippage_anomaly import evaluate_broker
    now = time.time_ns()
    for _ in range(10):
        _seed(temp_hot_store, "public", 5.0, now - 60_000_000_000)  # 60s ago
    r = evaluate_broker("public", now_ns=now)
    assert r["triggered"] is False
    assert "insufficient_baseline" in r["reason"]


def test_within_normal_range_no_trigger(temp_hot_store):
    from services.slippage_anomaly import evaluate_broker
    now = time.time_ns()
    # Baseline ~1.5 bps p50 over the past week.
    for i in range(30):
        _seed(temp_hot_store, "public", 1.5,
              now - int((2 * 86_400 + i * 60) * 1e9))
    # Recent ~2.0 bps (below 2× threshold of 3.0).
    for _ in range(10):
        _seed(temp_hot_store, "public", 2.0, now - 60_000_000_000)
    r = evaluate_broker("public", now_ns=now)
    assert r["triggered"] is False
    assert r["reason"] == "within_normal_range"
    assert r["threshold_bps"] == pytest.approx(3.0, rel=1e-6)


def test_over_threshold_triggers(temp_hot_store):
    from services.slippage_anomaly import evaluate_broker
    now = time.time_ns()
    # Baseline p50 = 2.0 bps.
    for _ in range(30):
        _seed(temp_hot_store, "public", 2.0,
              now - int(3 * 86_400 * 1e9))
    # Recent p50 = 6.0 bps → 3× baseline → over 2× threshold.
    for _ in range(10):
        _seed(temp_hot_store, "public", 6.0, now - 60_000_000_000)
    r = evaluate_broker("public", now_ns=now, multiplier=2.0)
    assert r["triggered"] is True
    assert r["reason"] == "recent_p50_over_threshold"
    assert r["recent_p50_bps"] == pytest.approx(6.0)
    assert r["baseline_p50_bps"] == pytest.approx(2.0)
    assert r["threshold_bps"] == pytest.approx(4.0)


def test_negative_baseline_disables_alert(temp_hot_store):
    """When historical slippage is in our favour, no threshold is
    meaningful and we return baseline_non_positive."""
    from services.slippage_anomaly import evaluate_broker
    now = time.time_ns()
    for _ in range(30):
        _seed(temp_hot_store, "public", -1.5,
              now - int(3 * 86_400 * 1e9))
    for _ in range(10):
        _seed(temp_hot_store, "public", 4.0, now - 60_000_000_000)
    r = evaluate_broker("public", now_ns=now)
    assert r["triggered"] is False
    assert r["reason"] == "baseline_non_positive"


def test_multiplier_is_honored(temp_hot_store):
    from services.slippage_anomaly import evaluate_broker
    now = time.time_ns()
    for _ in range(30):
        _seed(temp_hot_store, "public", 2.0, now - int(3 * 86_400 * 1e9))
    # Recent = 3.5 bps → 1.75× baseline. At 2× → below threshold. At 1.5× → over.
    for _ in range(10):
        _seed(temp_hot_store, "public", 3.5, now - 60_000_000_000)
    r_strict = evaluate_broker("public", now_ns=now, multiplier=2.0)
    r_lenient = evaluate_broker("public", now_ns=now, multiplier=1.5)
    assert r_strict["triggered"] is False
    assert r_lenient["triggered"] is True


def test_evaluate_all_returns_both_brokers(temp_hot_store):
    from services.slippage_anomaly import evaluate_all
    import asyncio
    now = time.time_ns()
    for _ in range(30):
        _seed(temp_hot_store, "public", 2.0, now - int(3 * 86_400 * 1e9))
    for _ in range(10):
        _seed(temp_hot_store, "public", 6.0, now - 60_000_000_000)

    async def run():
        return await evaluate_all(None)

    r = asyncio.run(run())
    brokers = {b["broker"]: b for b in r["brokers"]}
    assert set(brokers.keys()) == {"public", "moomoo"}
    assert brokers["public"]["triggered"] is True
    assert r["any_triggered"] is True


def test_empty_broker_never_triggers(temp_hot_store):
    from services.slippage_anomaly import evaluate_broker
    r = evaluate_broker("moomoo")
    assert r["triggered"] is False
    assert "insufficient_recent" in r["reason"]
    assert r["recent_samples"] == 0
    assert r["baseline_samples"] == 0
