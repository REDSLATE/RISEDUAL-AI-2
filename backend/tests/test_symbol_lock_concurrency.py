"""Regression tests for the concurrent double-buy fix.

Code review 2026-09-03 identified a HIGH severity race: the earlier
``try_acquire_symbol_lock`` had an "idempotent re-acquire when same
setup_id + same source" branch that let two concurrent scheduler
ticks (the 5-min ``alpha_day_trader`` and the new 60s
``alpha_top10_stream``) BOTH pass the lock and BOTH submit a broker
order for the same symbol.

This suite locks the fix down:

* A second acquire attempt on the same ``symbol`` — even with
  identical ``setup_id`` + ``source`` — must fail while the first
  holder still holds a non-expired lock.
* An expired lock is takeable by any caller (including the same).
* Explicit release opens the door for the next caller.
"""
from __future__ import annotations

import os
import tempfile

import pytest


@pytest.fixture(autouse=True)
def _isolated_hot_store(tmp_path, monkeypatch):
    """Point the SQLite hot store at a tmp file so tests don't
    stomp on each other or on the live pod store."""
    db_path = tmp_path / "alpha_hot_store.db"
    monkeypatch.setenv("ALPHA_HOT_STORE_PATH", str(db_path))
    # Force reload so the module picks up the new path.
    from services import alpha_hot_store
    alpha_hot_store._DB_PATH = None  # reset lazy path cache
    alpha_hot_store.init()
    yield
    try:
        os.unlink(db_path)
    except OSError:
        pass


def test_second_acquire_with_same_setup_id_now_blocks():
    """This is the double-buy regression. Before the fix the
    'idempotent re-acquire' branch let two concurrent ticks reading
    the same active setup both take the lock. Now the second
    caller must be refused.
    """
    from services import alpha_hot_store
    ok1 = alpha_hot_store.try_acquire_symbol_lock(
        "NFLX", setup_id="setup-abc", source="alpha_daytrader",
        ttl_seconds=120,
    )
    ok2 = alpha_hot_store.try_acquire_symbol_lock(
        "NFLX", setup_id="setup-abc", source="alpha_daytrader",
        ttl_seconds=120,
    )
    assert ok1 is True, "first tick must win the lock"
    assert ok2 is False, (
        "second tick with SAME setup_id + source must NOT re-acquire "
        "— this was the double-buy race the code review caught"
    )


def test_second_acquire_with_different_source_still_blocks():
    """Cross-scanner dedup is preserved (this behaviour predates
    the fix). alpha_daytrader holds → day_trade_scanner refused."""
    from services import alpha_hot_store
    ok1 = alpha_hot_store.try_acquire_symbol_lock(
        "NFLX", setup_id="setup-abc", source="alpha_daytrader",
        ttl_seconds=120,
    )
    ok2 = alpha_hot_store.try_acquire_symbol_lock(
        "NFLX", setup_id="setup-xyz", source="day_trade_scanner",
        ttl_seconds=120,
    )
    assert ok1 is True
    assert ok2 is False


def test_release_frees_lock_for_next_caller():
    """After the winner explicitly releases, the next caller (same
    or different setup) can take the lock."""
    from services import alpha_hot_store
    alpha_hot_store.try_acquire_symbol_lock(
        "NFLX", setup_id="setup-abc", source="alpha_daytrader",
    )
    alpha_hot_store.release_symbol_lock("NFLX", setup_id="setup-abc")
    ok2 = alpha_hot_store.try_acquire_symbol_lock(
        "NFLX", setup_id="setup-abc", source="alpha_daytrader",
    )
    assert ok2 is True


def test_expired_lock_is_takeable():
    """When the prior holder's TTL has passed, the lock is free
    for the taking."""
    from services import alpha_hot_store
    # 0-second TTL → immediately expired
    alpha_hot_store.try_acquire_symbol_lock(
        "NFLX", setup_id="setup-old", source="alpha_daytrader",
        ttl_seconds=0,
    )
    import time
    time.sleep(0.001)  # ensure the ns clock advances
    ok = alpha_hot_store.try_acquire_symbol_lock(
        "NFLX", setup_id="setup-new", source="alpha_daytrader",
        ttl_seconds=120,
    )
    assert ok is True


def test_different_symbols_do_not_conflict():
    """The lock is per-symbol; independent symbols don't block
    each other."""
    from services import alpha_hot_store
    ok1 = alpha_hot_store.try_acquire_symbol_lock(
        "NFLX", setup_id="a", source="alpha_daytrader",
    )
    ok2 = alpha_hot_store.try_acquire_symbol_lock(
        "AAPL", setup_id="b", source="alpha_daytrader",
    )
    assert ok1 is True
    assert ok2 is True
