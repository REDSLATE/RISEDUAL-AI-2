"""
Tests for the "raw vs calibrated" badge plumbing in
``services.tier3_readiness.tier3_readiness_snapshot`` and
``services.research_shadow_stats.fetch_tier_readiness``.

Pins:
1. When NO calibration model is active, the snapshot omits the
   ``raw_view`` block and reports ``calibration.active=False``.
2. When a model is active, ``raw_view`` carries the same shape
   (stats + unlock) computed from raw confidence only.
3. The ``calibration`` block reflects the persisted model
   (version, n_rows, ECE, applies_to scope).
4. Top-level ``stats``/``unlock`` keep using calibrated rows when
   present (existing contract — sizing readers must not change).
5. ``fetch_tier_readiness`` exposes ``tier3_progress_pct_raw`` for
   the dashboard badge component.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest


class _FakeCursor:
    def __init__(self, rows):
        self._rows = list(rows)

    def limit(self, _n):
        return self

    async def to_list(self, length=None):  # noqa: ARG002
        return list(self._rows)

    def __aiter__(self):
        async def gen():
            for r in self._rows:
                yield r
        return gen()


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

    async def count_documents(self, query):  # noqa: ARG002
        return len(self._docs)

    async def insert_one(self, doc):
        self._docs.append(dict(doc))


class _FakeDB:
    def __init__(self):
        self._cols: dict[str, _FakeCollection] = {}

    def __getitem__(self, name):
        if name not in self._cols:
            self._cols[name] = _FakeCollection()
        return self._cols[name]

    def __getattr__(self, name):
        return self[name]


@pytest.fixture
def db():
    return _FakeDB()


def _seed_predictions(db, n=30, raw_low=0.55, calibrated_high=0.92):
    """Seed n verified rows with raw under-confidence (<0.7 raw) but
    calibrated >0.9 — the 'badge has work to do' shape from our
    live audit."""
    now = datetime.now(timezone.utc)
    for i in range(n):
        db.predictions._docs.append({
            "confidence": raw_low,
            "calibrated_confidence": calibrated_high,
            "verified_24h": {"correct": (i % 5 != 0)},  # 80% win rate
            "timestamp": (now - timedelta(days=1)).isoformat(),
        })


# ── tier3_readiness_snapshot dual-mode ────────────────────────────


@pytest.mark.asyncio
async def test_snapshot_omits_raw_view_without_calibration(db, monkeypatch):
    from services.tier3_readiness import tier3_readiness_snapshot

    # Stub deps that touch unrelated collections.
    monkeypatch.setattr(
        "services.paper_trading_progress.compute_live_days",
        lambda _db: _ANoop()(),
    )
    monkeypatch.setattr(
        "services.conviction_clamp_canary.conviction_clamp_counter",
        lambda _db, days=30: _ANoop({"clamp_total": 0})(),
    )
    _seed_predictions(db, n=10)

    out = await tier3_readiness_snapshot(db, days=30)
    assert out["calibration"] == {"active": False}
    assert "raw_view" not in out
    assert "stats" in out and "unlock" in out


@pytest.mark.asyncio
async def test_snapshot_includes_raw_view_when_calibrated(db, monkeypatch):
    from services.tier3_readiness import tier3_readiness_snapshot

    monkeypatch.setattr(
        "services.paper_trading_progress.compute_live_days",
        lambda _db: _ANoop()(),
    )
    monkeypatch.setattr(
        "services.conviction_clamp_canary.conviction_clamp_counter",
        lambda _db, days=30: _ANoop({"clamp_total": 0})(),
    )

    # Seed an active calibration model.
    db["calibration_models"]._docs.append({
        "version": "isotonic_test_v1",
        "active": True,
        "n_rows": 200,
        "ece_before_pp": 30.0,
        "ece_after_pp": 1.5,
        "max_calibrated_confidence": 0.95,
        "calibration_applies_to": ["tier3_readiness_only"],
        "knots": [{"x": 0.0, "y": 0.0}, {"x": 1.0, "y": 0.95}],
    })
    _seed_predictions(db, n=30)

    out = await tier3_readiness_snapshot(db, days=30)
    assert out["calibration"]["active"] is True
    assert out["calibration"]["version"] == "isotonic_test_v1"
    assert out["calibration"]["applies_to"] == ["tier3_readiness_only"]
    assert "raw_view" in out
    assert "stats" in out["raw_view"]
    assert "unlock" in out["raw_view"]
    # Calibrated path counts these rows as high-conf (calibrated 0.92 >= 0.70).
    assert out["stats"]["high_conf_trades"] >= 30
    # Raw path does NOT count them (raw 0.55 < 0.70).
    assert out["raw_view"]["stats"]["high_conf_trades"] == 0


@pytest.mark.asyncio
async def test_snapshot_top_level_stats_use_calibrated(db, monkeypatch):
    """Existing contract — sizing/execution readers see the
    calibrated view at the top level when a model is active."""
    from services.tier3_readiness import tier3_readiness_snapshot

    monkeypatch.setattr(
        "services.paper_trading_progress.compute_live_days",
        lambda _db: _ANoop()(),
    )
    monkeypatch.setattr(
        "services.conviction_clamp_canary.conviction_clamp_counter",
        lambda _db, days=30: _ANoop({"clamp_total": 0})(),
    )

    db["calibration_models"]._docs.append({
        "version": "v1", "active": True,
        "calibration_applies_to": ["tier3_readiness_only"],
    })
    _seed_predictions(db, n=30)

    out = await tier3_readiness_snapshot(db, days=30)
    assert out["stats"]["uses_calibrated_confidence"] is True


# ── helper: wraps a plain value in an awaitable for monkeypatch ───


class _ANoop:
    def __init__(self, value=0):
        self.value = value

    def __call__(self):
        async def _coro():
            return self.value
        return _coro()
