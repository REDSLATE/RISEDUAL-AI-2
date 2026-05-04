"""
Tests for ``services.calibration_service``.

Pin the contracts the operator approved on 2026-05-04:

1. Pure-function ``apply_calibration`` returns ``None`` when the
   model is missing/empty/malformed (caller-safe).
2. ``apply_calibration`` is monotone non-decreasing — a higher raw
   input must never map to a lower calibrated value.
3. Calibrated values are clamped at ``MAX_CALIBRATED_CONFIDENCE``
   (default 0.95) so the readiness gate never sees 1.0.
4. ``fit_isotonic_calibration`` refuses below ``MIN_CALIBRATION_ROWS``
   (the prior model — if any — keeps serving).
5. Successful fit persists ``active=True``, demotes prior active row,
   and stamps ``calibration_applies_to=["tier3_readiness_only"]``.
6. ECE-after is no worse than ECE-before — the fit never makes the
   model less calibrated than it already was on the training corpus.
7. ``calibration_audit_fields`` returns ``{}`` when no model is
   active (so callers can ``doc.update`` it unconditionally).
8. ``calibration_audit_fields`` returns the full audit envelope
   when a model is active — including the explicit
   ``calibration_applies_to`` boundary.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

import pytest


class _FakeCursor:
    def __init__(self, rows):
        self._rows = list(rows)

    def limit(self, _n):
        return self

    async def to_list(self, length=None):  # noqa: ARG002
        return list(self._rows)


class _FakeCollection:
    def __init__(self):
        self._docs: list[dict] = []

    def find(self, query, projection=None):  # noqa: ARG002
        rows = []
        for d in self._docs:
            ok = True
            for k, v in query.items():
                if isinstance(v, dict):
                    if "$gte" in v:
                        if d.get(k) is None or d[k] < v["$gte"]:
                            ok = False
                            break
                    if "$in" in v:
                        # Path-walk for dotted keys like
                        # ``verified_24h.correct``.
                        cur = d
                        for part in k.split("."):
                            if not isinstance(cur, dict):
                                cur = None
                                break
                            cur = cur.get(part)
                        if cur not in v["$in"]:
                            ok = False
                            break
                else:
                    if d.get(k) != v:
                        ok = False
                        break
            if ok:
                rows.append(dict(d))
        return _FakeCursor(rows)

    async def find_one(self, query, projection=None):  # noqa: ARG002
        for d in self._docs:
            if all(d.get(k) == v for k, v in query.items()):
                return dict(d)
        return None

    async def insert_one(self, doc):
        self._docs.append(dict(doc))

    async def update_one(self, query, update):
        for d in self._docs:
            if all(d.get(k) == v for k, v in query.items()):
                if "$set" in update:
                    d.update(update["$set"])
                break

    async def update_many(self, query, update):
        for d in self._docs:
            if all(d.get(k) == v for k, v in query.items()):
                if "$set" in update:
                    d.update(update["$set"])


class _FakeDB:
    def __init__(self):
        self._cols: dict[str, _FakeCollection] = {}

    def __getitem__(self, name):
        if name not in self._cols:
            self._cols[name] = _FakeCollection()
        return self._cols[name]

    def __getattr__(self, name):
        # Allow ``db.predictions`` attribute-style access.
        return self[name]


@pytest.fixture
def db():
    return _FakeDB()


def _seed_predictions(db, *, n=160, seed=2026, scale=1.0):
    """Seed a corpus with chronic under-confidence: raw ~0.55-0.65,
    actual win rate ~0.90. Mirrors the live data we audited."""
    rng = random.Random(seed)
    now = datetime.now(timezone.utc)
    for i in range(n):
        raw = rng.uniform(0.50, 0.70) * scale
        # 90% true win rate regardless of confidence (exact pattern
        # of an under-confident model).
        correct = rng.random() < 0.90
        db.predictions._docs.append({
            "confidence": raw,
            "verified_24h": {"correct": correct},
            "timestamp": (now - timedelta(days=rng.randint(1, 60))).isoformat(),
        })


# ── apply_calibration purity & boundaries ─────────────────────────


def test_apply_returns_none_when_model_absent():
    from services.calibration_service import apply_calibration
    assert apply_calibration(0.6, None) is None
    assert apply_calibration(0.6, {}) is None
    assert apply_calibration(0.6, {"knots": []}) is None


def test_apply_monotone_non_decreasing():
    """Synthetic 2-knot model: raw 0 → 0, raw 1 → 0.95."""
    from services.calibration_service import apply_calibration
    model = {
        "knots": [{"x": 0.0, "y": 0.0}, {"x": 1.0, "y": 0.95}],
        "max_calibrated_confidence": 0.95,
    }
    prev = -1.0
    for x in [0.0, 0.1, 0.3, 0.5, 0.6, 0.7, 0.8, 0.95, 1.0]:
        y = apply_calibration(x, model)
        assert y is not None
        assert y >= prev, f"non-monotone at x={x}: {y} < {prev}"
        prev = y


def test_apply_caps_at_max_calibrated():
    from services.calibration_service import apply_calibration
    # Knot tries to map raw 0.5 to 0.99; cap should bring it to 0.95.
    model = {
        "knots": [{"x": 0.0, "y": 0.0}, {"x": 0.5, "y": 0.99}, {"x": 1.0, "y": 0.99}],
        "max_calibrated_confidence": 0.95,
    }
    assert apply_calibration(0.5, model) <= 0.95
    assert apply_calibration(0.9, model) <= 0.95


def test_apply_handles_0_to_100_scale_input():
    from services.calibration_service import apply_calibration
    model = {
        "knots": [{"x": 0.0, "y": 0.0}, {"x": 1.0, "y": 0.95}],
        "max_calibrated_confidence": 0.95,
    }
    # Input 60 (on 0-100 scale) should fold to 0.6 internally.
    y_pct = apply_calibration(60.0, model)
    y_unit = apply_calibration(0.6, model)
    assert y_pct == y_unit


# ── fit_isotonic_calibration ──────────────────────────────────────


@pytest.mark.asyncio
async def test_fit_refuses_below_min_rows(db):
    from services.calibration_service import fit_isotonic_calibration
    _seed_predictions(db, n=50)
    out = await fit_isotonic_calibration(db, min_rows=100)
    assert out is None
    # No row should have been inserted into calibration_models.
    assert len(db["calibration_models"]._docs) == 0


@pytest.mark.asyncio
async def test_fit_persists_active_and_demotes_prior(db):
    from services.calibration_service import fit_isotonic_calibration
    # Seed an existing "active" model.
    db["calibration_models"]._docs.append({
        "version": "isotonic_v_old", "active": True,
        "calibration_applies_to": ["tier3_readiness_only"],
    })
    _seed_predictions(db, n=200)

    new = await fit_isotonic_calibration(db, min_rows=100)
    assert new is not None
    assert new["active"] is True
    assert new["calibration_applies_to"] == ["tier3_readiness_only"]
    # Prior model is now inactive.
    prior = await db["calibration_models"].find_one({"version": "isotonic_v_old"})
    assert prior["active"] is False


@pytest.mark.asyncio
async def test_fit_improves_ece(db):
    """The fit must NOT make calibration worse than it was on the
    training corpus. Under-confident corpus → ECE should drop."""
    from services.calibration_service import fit_isotonic_calibration
    _seed_predictions(db, n=300)

    out = await fit_isotonic_calibration(db, min_rows=100)
    assert out is not None
    assert out["ece_after_pp"] <= out["ece_before_pp"] + 0.01


@pytest.mark.asyncio
async def test_fit_caps_calibrated_at_max(db):
    """Even with a perfect-win corpus, knot y values must respect
    ``max_calibrated_confidence``."""
    from services.calibration_service import fit_isotonic_calibration
    # 100% win rate corpus.
    now = datetime.now(timezone.utc)
    for i in range(150):
        db.predictions._docs.append({
            "confidence": 0.50 + (i % 10) * 0.01,
            "verified_24h": {"correct": True},
            "timestamp": (now - timedelta(days=1)).isoformat(),
        })

    out = await fit_isotonic_calibration(db, min_rows=100, max_calibrated=0.95)
    assert out is not None
    assert all(k["y"] <= 0.95 for k in out["knots"])
    assert out["max_calibrated_confidence"] == 0.95


# ── calibration_audit_fields ──────────────────────────────────────


def test_audit_fields_empty_when_no_model():
    from services.calibration_service import calibration_audit_fields
    assert calibration_audit_fields(0.6, None) == {}
    assert calibration_audit_fields(0.6, {}) == {}


def test_audit_fields_full_envelope_when_model_active():
    from services.calibration_service import calibration_audit_fields
    model = {
        "version": "isotonic_test_v1",
        "n_rows": 147,
        "knots": [{"x": 0.0, "y": 0.0}, {"x": 1.0, "y": 0.95}],
        "max_calibrated_confidence": 0.95,
        "calibration_applies_to": ["tier3_readiness_only"],
        "calibration_source": "prediction_24h_verified_rows",
    }
    out = calibration_audit_fields(0.6, model)
    assert out["calibration_version"] == "isotonic_test_v1"
    assert out["calibration_n"] == 147
    assert out["calibration_applies_to"] == ["tier3_readiness_only"]
    assert out["calibration_source"] == "prediction_24h_verified_rows"
    assert 0.0 <= out["calibrated_confidence"] <= 0.95


# ── get_active_calibration ────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_active_returns_none_for_empty_db(db):
    from services.calibration_service import get_active_calibration
    assert await get_active_calibration(db) is None


@pytest.mark.asyncio
async def test_get_active_returns_only_active_row(db):
    from services.calibration_service import get_active_calibration
    db["calibration_models"]._docs.append({
        "version": "v_old", "active": False,
    })
    db["calibration_models"]._docs.append({
        "version": "v_new", "active": True,
    })
    out = await get_active_calibration(db)
    assert out["version"] == "v_new"


@pytest.mark.asyncio
async def test_get_active_handles_none_db():
    from services.calibration_service import get_active_calibration
    assert await get_active_calibration(None) is None
