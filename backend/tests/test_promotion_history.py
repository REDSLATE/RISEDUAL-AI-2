"""Tests for ``services.promotion_history``.

Critical invariants:

1. Cold-start for a core seeds a single ``(initial) → current`` row.
2. No delta since last boot → zero writes (idempotent).
3. Env phase change since last boot → one transition row.
4. Manual ``record_promotion`` round-trips cleanly.
5. ``get_promotion_history`` filters by core and limits correctly.
"""
from __future__ import annotations

import pytest

from services.promotion_history import (
    COLLECTION,
    CORE_REGISTRY,
    detect_phase_changes_at_startup,
    get_promotion_history,
    record_promotion,
)


# ── In-memory Mongo-ish fake ───────────────────────────────────────


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows
        self._sort = None
        self._limit_n = None

    def sort(self, key, direction=-1):
        self._sort = (key, direction)
        return self

    def limit(self, n):
        self._limit_n = n
        return self

    async def to_list(self, length=None):  # noqa: ARG002
        rows = list(self._rows)
        if self._sort:
            key, direction = self._sort
            if isinstance(key, str):
                rows.sort(key=lambda r: r.get(key) or 0, reverse=(direction == -1))
            elif isinstance(key, list) and key and key[0] == ("at", -1):
                rows.sort(key=lambda r: r.get("at") or 0, reverse=True)
        if self._limit_n is not None:
            rows = rows[: self._limit_n]
        return rows


class _FakeCollection:
    def __init__(self):
        self.rows: list[dict] = []

    async def insert_one(self, doc):
        self.rows.append(dict(doc))

    def find(self, query, projection=None):  # noqa: ARG002
        matched = [r for r in self.rows if all(r.get(k) == v for k, v in query.items())]
        return _FakeCursor(matched)

    async def find_one(self, query, projection=None, sort=None):  # noqa: ARG002
        matched = [r for r in self.rows if all(r.get(k) == v for k, v in query.items())]
        if sort == [("at", -1)]:
            matched.sort(key=lambda r: r.get("at") or 0, reverse=True)
        return matched[0] if matched else None


class _FakeDB:
    def __init__(self):
        self._colls: dict = {}

    def __getitem__(self, name):
        if name not in self._colls:
            self._colls[name] = _FakeCollection()
        return self._colls[name]


# ── Cold-start detection ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_cold_start_seeds_initial_row_per_core(monkeypatch):
    """Fresh DB → detector writes one ``(initial) → phase`` row per core."""
    # Pin env phases to known values.
    for core, cfg in CORE_REGISTRY.items():
        monkeypatch.setenv(cfg["env_var"], cfg["default_phase"])

    db = _FakeDB()
    inserted = await detect_phase_changes_at_startup(db)

    assert len(inserted) == len(CORE_REGISTRY)
    for row in inserted:
        assert row["from_phase"] == "(initial)"
        assert row["actor"] == "auto_boot_detector"
        assert row["reason"] == "initial_phase_seeded_at_boot"
        assert row["core"] in CORE_REGISTRY


@pytest.mark.asyncio
async def test_second_boot_no_delta_writes_nothing(monkeypatch):
    for core, cfg in CORE_REGISTRY.items():
        monkeypatch.setenv(cfg["env_var"], cfg["default_phase"])

    db = _FakeDB()
    await detect_phase_changes_at_startup(db)  # first boot seeds.
    starting = len(db[COLLECTION].rows)

    inserted_second = await detect_phase_changes_at_startup(db)
    assert inserted_second == []
    assert len(db[COLLECTION].rows) == starting


@pytest.mark.asyncio
async def test_env_phase_change_records_transition(monkeypatch):
    """Cold-start → flip one env var → second boot writes one delta."""
    for core, cfg in CORE_REGISTRY.items():
        monkeypatch.setenv(cfg["env_var"], cfg["default_phase"])

    db = _FakeDB()
    await detect_phase_changes_at_startup(db)  # seed.

    # Flip the adversarial phase as if operator edited .env + restarted.
    monkeypatch.setenv(CORE_REGISTRY["adversarial"]["env_var"], "risk_only")

    inserted = await detect_phase_changes_at_startup(db)
    assert len(inserted) == 1
    row = inserted[0]
    assert row["core"] == "adversarial"
    assert row["from_phase"] == CORE_REGISTRY["adversarial"]["default_phase"]
    assert row["to_phase"] == "risk_only"
    assert row["actor"] == "auto_boot_detector"


# ── record_promotion + get_promotion_history ───────────────────────


@pytest.mark.asyncio
async def test_record_promotion_round_trip():
    db = _FakeDB()
    row = await record_promotion(
        db, core="adversarial",
        from_phase="shadow", to_phase="risk_only",
        actor="owner@risedual.ai", reason="rate_cleared_60pct",
        metrics={"closed_lifetime": 42},
    )
    assert row["core"] == "adversarial"
    assert row["from_phase"] == "shadow"
    assert row["to_phase"] == "risk_only"
    assert row["metrics"]["closed_lifetime"] == 42

    history = await get_promotion_history(db, limit=10)
    assert len(history) == 1
    assert history[0]["actor"] == "owner@risedual.ai"


@pytest.mark.asyncio
async def test_get_promotion_history_filters_by_core():
    db = _FakeDB()
    await record_promotion(
        db, core="adversarial",
        from_phase="shadow", to_phase="risk_only",
    )
    await record_promotion(
        db, core="sovereign_equity",
        from_phase="phase_1", to_phase="phase_2",
    )
    await record_promotion(
        db, core="adversarial",
        from_phase="risk_only", to_phase="veto",
    )

    adv_rows = await get_promotion_history(db, core="adversarial")
    assert len(adv_rows) == 2
    assert all(r["core"] == "adversarial" for r in adv_rows)

    sov_rows = await get_promotion_history(db, core="sovereign_equity")
    assert len(sov_rows) == 1


@pytest.mark.asyncio
async def test_get_promotion_history_respects_limit():
    db = _FakeDB()
    for i in range(5):
        await record_promotion(
            db, core="adversarial",
            from_phase=f"p{i}", to_phase=f"p{i + 1}",
        )
    rows = await get_promotion_history(db, limit=3)
    assert len(rows) == 3


@pytest.mark.asyncio
async def test_record_promotion_survives_null_db():
    """Never raises when DB isn't available."""
    row = await record_promotion(
        None, core="adversarial",
        from_phase="shadow", to_phase="risk_only",
    )
    # Row is still assembled, just not persisted.
    assert row["core"] == "adversarial"


@pytest.mark.asyncio
async def test_get_promotion_history_empty_on_null_db():
    rows = await get_promotion_history(None)
    assert rows == []
