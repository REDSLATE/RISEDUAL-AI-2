"""Index contract for ``AICacheService``.

The cache hot-path lives or dies on its indexes:

* Every ``get`` does ``find_one({"cache_key": ..., "expires_at": ...})``
  — without an index this is a collection scan, which gets brutal as
  soon as the cache grows past a few thousand entries (RISEDUAL is a
  high-frequency signal app, this happens in days).
* Every ``set`` does ``replace_one({"cache_key": ...}, upsert=True)``
  — without a UNIQUE index on ``cache_key`` two concurrent writes for
  the same key can race and produce duplicate documents.
* Stale entries should self-prune via a Mongo TTL index on
  ``expires_at`` so we don't depend on the application-level
  ``cleanup_expired`` job ever running.

These tests pin the contract so any future "small refactor" to the
cache service that drops an index fails loudly here.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.ai_cache_service import AICacheService


def _fake_db(*, existing_indexes: dict | None = None):
    fake_collection = MagicMock()
    fake_collection.create_index = AsyncMock(return_value="ok")
    fake_collection.drop_index = AsyncMock(return_value="ok")
    fake_collection.index_information = AsyncMock(
        return_value=existing_indexes or {"_id_": {"key": [("_id", 1)]}}
    )
    fake_collection.find_one = AsyncMock(return_value=None)
    fake_collection.update_one = AsyncMock(return_value=None)
    fake_collection.replace_one = AsyncMock(return_value=None)
    fake_db = MagicMock()
    fake_db.__getitem__.return_value = fake_collection
    return fake_db, fake_collection


# ─── ensure_indexes: required indexes ────────────────────────────────


@pytest.mark.asyncio
async def test_ensure_indexes_creates_unique_cache_key_index():
    db, collection = _fake_db()
    await AICacheService.ensure_indexes(db)

    calls = [c.kwargs for c in collection.create_index.call_args_list]
    args = [c.args for c in collection.create_index.call_args_list]

    # Find the cache_key call.
    cache_key_calls = [
        (a, k) for a, k in zip(args, calls) if a == ("cache_key",)
    ]
    assert cache_key_calls, "cache_key index was not created"
    _, kwargs = cache_key_calls[0]
    assert kwargs.get("unique") is True, (
        "cache_key index must be UNIQUE — race-safe upserts depend on it"
    )
    # Name must be the conventional Mongo default so re-running
    # against an existing deployment is idempotent (the bug we just
    # fixed: a different ``name=...`` triggered IndexOptionsConflict).
    assert kwargs.get("name") == "cache_key_1"


@pytest.mark.asyncio
async def test_ensure_indexes_creates_ttl_index_on_expires_at():
    db, collection = _fake_db()
    await AICacheService.ensure_indexes(db)

    args = [c.args for c in collection.create_index.call_args_list]
    kwargs = [c.kwargs for c in collection.create_index.call_args_list]

    # Find the expires_at call.
    ttl_calls = [
        (a, k) for a, k in zip(args, kwargs) if a == ("expires_at",)
    ]
    assert ttl_calls, "expires_at index was not created"
    _, k = ttl_calls[0]
    # A TTL index must declare expireAfterSeconds. The =0 form means
    # "delete as soon as the stored timestamp is in the past".
    assert k.get("expireAfterSeconds") == 0, (
        "expires_at must be a TTL index with expireAfterSeconds=0 so "
        "Mongo auto-prunes stale entries without app-level cleanup."
    )


@pytest.mark.asyncio
async def test_ensure_indexes_creates_created_at_descending():
    db, collection = _fake_db()
    await AICacheService.ensure_indexes(db)

    # created_at index uses the (field, direction) tuple form.
    args = [c.args for c in collection.create_index.call_args_list]
    created_at_calls = [
        a for a in args if a and isinstance(a[0], list)
        and a[0] == [("created_at", -1)]
    ]
    assert created_at_calls, (
        "created_at descending index missing — get_stale() and "
        "max_age_seconds filtering will scan the collection without it."
    )


@pytest.mark.asyncio
async def test_ensure_indexes_is_idempotent_no_op_on_none_db():
    """Some test contexts wire the service before the DB is ready;
    that path must not raise."""
    await AICacheService.ensure_indexes(None)


@pytest.mark.asyncio
async def test_ensure_indexes_swallows_index_creation_errors():
    """Mongo can return all kinds of transient errors here; this must
    never block app startup."""
    db, collection = _fake_db()
    collection.create_index = AsyncMock(side_effect=RuntimeError("boom"))
    # Should not raise.
    await AICacheService.ensure_indexes(db)


@pytest.mark.asyncio
async def test_one_index_failure_does_not_block_the_others():
    """Per-index independent try/except is the whole point of the
    rewrite — a conflict on cache_key must not skip the TTL index."""
    db, collection = _fake_db()
    side_effects = []

    async def selective_create_index(*args, **kwargs):
        # First call (cache_key) raises; subsequent calls succeed.
        if not side_effects:
            side_effects.append(args)
            raise RuntimeError("IndexOptionsConflict")
        side_effects.append(args)
        return "ok"

    collection.create_index = AsyncMock(side_effect=selective_create_index)
    await AICacheService.ensure_indexes(db)

    # cache_key + expires_at + created_at — three independent calls.
    assert len(side_effects) == 3, (
        "create_index must be called per-index independently; instead "
        f"got {len(side_effects)} call(s)."
    )


@pytest.mark.asyncio
async def test_legacy_non_ttl_expires_at_index_is_dropped_before_ttl_recreate():
    """Pin the migration path: a prior non-TTL ``expires_at_1`` index
    must be removed before the new TTL index is created, otherwise
    Mongo raises IndexOptionsConflict and the TTL never lands."""
    db, collection = _fake_db(existing_indexes={
        "_id_": {"key": [("_id", 1)]},
        "cache_key_1": {"key": [("cache_key", 1)], "unique": True},
        # Legacy non-TTL index — note: NO expireAfterSeconds.
        "expires_at_1": {"key": [("expires_at", 1)]},
    })
    await AICacheService.ensure_indexes(db)

    drop_calls = [c.args for c in collection.drop_index.call_args_list]
    assert ("expires_at_1",) in drop_calls, (
        "Legacy non-TTL expires_at index must be dropped to allow the "
        "TTL upgrade."
    )


@pytest.mark.asyncio
async def test_existing_ttl_expires_at_index_is_left_alone():
    """If the TTL index is already correct, do NOT drop it."""
    db, collection = _fake_db(existing_indexes={
        "_id_": {"key": [("_id", 1)]},
        "expires_at_ttl": {
            "key": [("expires_at", 1)], "expireAfterSeconds": 0,
        },
    })
    await AICacheService.ensure_indexes(db)

    drop_calls = [c.args for c in collection.drop_index.call_args_list]
    # Only the stale-cleanup pass may drop indexes — and that's just
    # ``endpoint_1`` which isn't present here.
    assert all(c != ("expires_at_ttl",) for c in drop_calls)


@pytest.mark.asyncio
async def test_stale_endpoint_index_is_cleaned_up():
    db, collection = _fake_db(existing_indexes={
        "_id_": {"key": [("_id", 1)]},
        "endpoint_1": {"key": [("endpoint", 1)]},
    })
    await AICacheService.ensure_indexes(db)

    drop_calls = [c.args for c in collection.drop_index.call_args_list]
    assert ("endpoint_1",) in drop_calls


# ─── hot-path queries actually use the indexed fields ────────────────


@pytest.mark.asyncio
async def test_get_query_filters_on_indexed_fields_only():
    """If get() ever starts filtering on an unindexed field, a
    collection scan creeps in. Pin the query shape."""
    db, collection = _fake_db()
    collection.find_one = AsyncMock(return_value={"data": {"x": 1}})

    service = AICacheService(db)
    await service.get("k", max_age_seconds=60)

    args, _ = collection.find_one.call_args
    query = args[0]
    # Every filter key in this query must be in the indexed set.
    indexed_fields = {"cache_key", "expires_at", "created_at"}
    assert set(query.keys()) <= indexed_fields, (
        f"get() introduced a filter on an unindexed field: "
        f"{set(query.keys()) - indexed_fields}"
    )


@pytest.mark.asyncio
async def test_set_uses_replace_one_with_cache_key_filter_for_upsert_safety():
    db, collection = _fake_db()
    service = AICacheService(db)
    await service.set("k", "ns", {"v": 1}, ttl_seconds=300)

    collection.replace_one.assert_awaited_once()
    args, kwargs = collection.replace_one.call_args
    filter_doc = args[0]
    assert "cache_key" in filter_doc, (
        "set() must filter by cache_key so the unique index makes the "
        "upsert race-safe."
    )
    assert kwargs.get("upsert") is True
