"""Pytest coverage for the in-memory L1 cache + the hot-path index
migration. Mongo migration is mocked so the test is fast and offline.
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock

import pytest

from services.llm_response_cache import (
    DEFAULT_MAX_ENTRIES,
    DEFAULT_TTL_SECONDS,
    InMemoryL1Cache,
    hypothesis_l1_cache,
)


# ── L1 cache: basic ────────────────────────────────────────────────


def test_l1_constructor_validates_max_entries() -> None:
    with pytest.raises(ValueError):
        InMemoryL1Cache(max_entries=0)
    with pytest.raises(ValueError):
        InMemoryL1Cache(max_entries=-3)


def test_l1_constructor_validates_ttl() -> None:
    with pytest.raises(ValueError):
        InMemoryL1Cache(default_ttl_seconds=0)
    with pytest.raises(ValueError):
        InMemoryL1Cache(default_ttl_seconds=-5)


def test_l1_set_then_get_returns_value() -> None:
    c = InMemoryL1Cache()
    c.set("k", {"verdict": "BUY"})
    assert c.get("k") == {"verdict": "BUY"}


def test_l1_get_unknown_returns_none() -> None:
    c = InMemoryL1Cache()
    assert c.get("nope") is None


def test_l1_empty_key_is_safe() -> None:
    c = InMemoryL1Cache()
    c.set("", "v")  # no-op
    assert c.get("") is None


def test_l1_set_with_zero_ttl_is_no_op() -> None:
    c = InMemoryL1Cache()
    c.set("k", "v", ttl_seconds=0)
    assert c.get("k") is None
    c.set("k", "v", ttl_seconds=-1)
    assert c.get("k") is None


def test_l1_overwrite_replaces_value() -> None:
    c = InMemoryL1Cache()
    c.set("k", "v1")
    c.set("k", "v2")
    assert c.get("k") == "v2"


def test_l1_invalidate_removes_value() -> None:
    c = InMemoryL1Cache()
    c.set("k", "v")
    assert c.invalidate("k") is True
    assert c.get("k") is None
    assert c.invalidate("k") is False  # second time = nothing to remove
    assert c.invalidate("") is False


def test_l1_clear_drops_everything() -> None:
    c = InMemoryL1Cache()
    for i in range(5):
        c.set(f"k{i}", i)
    assert len(c) == 5
    c.clear()
    assert len(c) == 0


# ── L1 cache: TTL ──────────────────────────────────────────────────


def test_l1_expired_entry_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    c = InMemoryL1Cache(default_ttl_seconds=1)
    fake_time = [1000.0]
    monkeypatch.setattr("time.monotonic", lambda: fake_time[0])
    c.set("k", "v")
    assert c.get("k") == "v"
    fake_time[0] += 2.0  # past TTL
    assert c.get("k") is None
    # Expired entry was dropped on read.
    assert "k" not in c._store


def test_l1_custom_ttl_overrides_default(monkeypatch: pytest.MonkeyPatch) -> None:
    c = InMemoryL1Cache(default_ttl_seconds=60)
    fake_time = [1000.0]
    monkeypatch.setattr("time.monotonic", lambda: fake_time[0])
    c.set("k", "v", ttl_seconds=5)
    fake_time[0] += 10  # past 5s TTL, well within 60s default
    assert c.get("k") is None


# ── L1 cache: LRU ──────────────────────────────────────────────────


def test_l1_lru_eviction_when_over_cap() -> None:
    c = InMemoryL1Cache(max_entries=3, default_ttl_seconds=300)
    c.set("a", 1)
    c.set("b", 2)
    c.set("c", 3)
    c.set("d", 4)  # should evict 'a'
    assert c.get("a") is None
    assert c.get("b") == 2
    assert c.get("c") == 3
    assert c.get("d") == 4


def test_l1_access_promotes_to_mru() -> None:
    c = InMemoryL1Cache(max_entries=3, default_ttl_seconds=300)
    c.set("a", 1)
    c.set("b", 2)
    c.set("c", 3)
    c.get("a")  # promote a → most-recently-used
    c.set("d", 4)  # should evict 'b' (oldest), NOT 'a'
    assert c.get("a") == 1
    assert c.get("b") is None
    assert c.get("c") == 3
    assert c.get("d") == 4


def test_l1_overwrite_does_not_increase_size() -> None:
    c = InMemoryL1Cache(max_entries=2)
    c.set("a", 1)
    c.set("b", 2)
    c.set("a", 99)  # overwrite — size stays 2
    assert len(c) == 2
    assert c.get("a") == 99
    assert c.get("b") == 2


# ── L1 cache: stats ────────────────────────────────────────────────


def test_l1_stats_track_hits_and_misses() -> None:
    c = InMemoryL1Cache()
    c.set("k", "v")
    c.get("k")  # hit
    c.get("k")  # hit
    c.get("missing")  # miss
    s = c.stats()
    assert s["hits"] == 2
    assert s["misses"] == 1
    assert s["hit_rate"] == pytest.approx(2 / 3)
    assert s["entries"] == 1


def test_l1_stats_count_evictions() -> None:
    c = InMemoryL1Cache(max_entries=2)
    c.set("a", 1)
    c.set("b", 2)
    c.set("c", 3)  # eviction #1
    c.set("d", 4)  # eviction #2
    assert c.stats()["evictions"] == 2


def test_l1_module_singleton_is_configured() -> None:
    assert hypothesis_l1_cache is not None
    s = hypothesis_l1_cache.stats()
    assert s["max_entries"] == 256
    assert s["default_ttl_seconds"] == 60


# ── L1 cache: thread safety smoke ──────────────────────────────────


def test_l1_concurrent_writes_dont_corrupt() -> None:
    import threading
    c = InMemoryL1Cache(max_entries=1000, default_ttl_seconds=300)
    threads: list[threading.Thread] = []
    for i in range(20):
        def worker(start: int = i * 10) -> None:
            for j in range(10):
                c.set(f"k{start + j}", start + j)
        t = threading.Thread(target=worker)
        threads.append(t)
        t.start()
    for t in threads:
        t.join()
    assert len(c) == 200
    for i in range(200):
        assert c.get(f"k{i}") == i


# ── Hot-path index migration ───────────────────────────────────────


@pytest.mark.asyncio
async def test_index_migration_handles_none_db() -> None:
    from migrations.add_hot_path_indexes import run
    out = await run(None)
    assert out["ok"] is False
    assert "reason" in out


@pytest.mark.asyncio
async def test_index_migration_creates_all_specced_indexes() -> None:
    from migrations.add_hot_path_indexes import run, SPECS

    calls: list[tuple[str, list, str]] = []

    class _FakeCollection:
        def __init__(self, name: str):
            self._name = name

        async def create_index(self, keys, name=None, background=False):
            calls.append((self._name, keys, name))
            return name

    class _FakeDB:
        def __getitem__(self, name):
            return _FakeCollection(name)

    out = await run(_FakeDB())
    assert out["ok"] is True
    total_specced = sum(len(v) for v in SPECS.values())
    assert out["created"] == total_specced
    assert out["skipped_existing"] == 0
    assert out["failures"] == []

    # Every (collection, name) pair was hit exactly once.
    seen = {(c, n) for c, _, n in calls}
    expected = {(coll, s["name"]) for coll, specs in SPECS.items() for s in specs}
    assert seen == expected


@pytest.mark.asyncio
async def test_index_migration_treats_existing_as_success() -> None:
    """``already exists`` errors are idempotent — they count as skipped."""
    from migrations.add_hot_path_indexes import run

    class _FakeCollection:
        async def create_index(self, *_a, **_kw):
            raise RuntimeError("index already exists with different options")

    class _FakeDB:
        def __getitem__(self, _name):
            return _FakeCollection()

    out = await run(_FakeDB())
    assert out["ok"] is True
    assert out["created"] == 0
    assert out["skipped_existing"] > 0
    assert out["failures"] == []


@pytest.mark.asyncio
async def test_index_migration_collects_real_failures() -> None:
    """Non-idempotent failures land in the failures list, ``ok=False``."""
    from migrations.add_hot_path_indexes import run

    class _FakeCollection:
        async def create_index(self, *_a, **_kw):
            raise RuntimeError("disk full")

    class _FakeDB:
        def __getitem__(self, _name):
            return _FakeCollection()

    out = await run(_FakeDB())
    assert out["ok"] is False
    assert out["created"] == 0
    assert len(out["failures"]) > 0
    assert "disk full" in out["failures"][0]


@pytest.mark.asyncio
async def test_index_migration_registered_in_runner() -> None:
    """The runner must know about the migration so it gets executed
    on the next startup. ID must be unique forever."""
    from services.migration_runner import _MIGRATIONS
    ids = [m[0] for m in _MIGRATIONS]
    assert "2026-05-14-add-hot-path-indexes" in ids
    # No duplicate IDs in the whole registry.
    assert len(ids) == len(set(ids))
