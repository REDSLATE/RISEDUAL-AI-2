"""
Tests for ``services.adversarial_monitor.summarize_24h``.

Pins the shape + aggregation math of the 24h rollup that feeds the
admin Terminal tab's Adversarial Cores chip.

Covers:
1. ``db=None`` returns a valid empty envelope, not a crash.
2. Env flag off → ``enabled=False`` regardless of row state.
3. Empty collection → valid empty envelope (cold-start is normal).
4. Aggregation math — counts by decision, wins by agent, avg
   edge_gap / bull conf / bear conf all correct on a seeded set.
5. Win-rate is ``None`` until at least one closed row exists
   (no false-precision on a cold-start feed).
6. Rows older than 24h are excluded.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest


class _FakeCursor:
    def __init__(self, rows):
        self._rows = list(rows)

    def sort(self, *_args, **_kwargs):
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
                if isinstance(v, dict) and "$gte" in v:
                    if d.get(k) is None or d[k] < v["$gte"]:
                        ok = False
                        break
                elif d.get(k) != v:
                    ok = False
                    break
            if ok:
                rows.append(d)
        return _FakeCursor(rows)

    async def insert_many(self, docs):
        self._docs.extend([dict(d) for d in docs])

    async def insert_one(self, doc):
        self._docs.append(dict(doc))


class _FakeDB:
    def __init__(self):
        self._cols: dict[str, _FakeCollection] = {}

    def __getitem__(self, name):
        if name not in self._cols:
            self._cols[name] = _FakeCollection()
        return self._cols[name]


@pytest.fixture
def db():
    return _FakeDB()


def _row(
    *, symbol="BTC", decision="LONG", bull_conf=0.7, bear_conf=0.4,
    edge_gap=0.3, age_minutes=30, winner=None, loser=None,
    final_r=None, phase="shadow",
) -> dict:
    now = datetime.now(timezone.utc)
    out = {
        "decision_id": f"d-{symbol}-{age_minutes}",
        "symbol": symbol,
        "phase": phase,
        "decision": decision,
        "bull_case": {"confidence": bull_conf},
        "bear_case": {"confidence": bear_conf},
        "edge_gap": edge_gap,
        "created_at": now - timedelta(minutes=age_minutes),
    }
    if winner is not None:
        out["winner"] = winner
        out["loser"] = loser
        out["final_result_r"] = final_r
        out["closed_at"] = now - timedelta(minutes=max(age_minutes - 10, 1))
    return out


@pytest.mark.asyncio
async def test_summarize_24h_none_db_returns_empty_envelope():
    from services.adversarial_monitor import summarize_24h

    out = await summarize_24h(None)
    assert out["decisions_24h"] == 0
    assert out["decision_counts"] == {"LONG": 0, "SHORT_OR_AVOID": 0, "NO_TRADE": 0}
    assert out["bull_win_rate"] is None
    assert out["bear_win_rate"] is None


@pytest.mark.asyncio
async def test_summarize_24h_env_off_marks_disabled(db, monkeypatch):
    from services.adversarial_monitor import summarize_24h

    monkeypatch.delenv("CRYPTO_ADVERSARIAL_ENABLED", raising=False)
    out = await summarize_24h(db)
    assert out["enabled"] is False
    # Phase defaults to shadow even when disabled.
    assert out["phase"] == "shadow"


@pytest.mark.asyncio
async def test_summarize_24h_env_on_empty_collection(db, monkeypatch):
    from services.adversarial_monitor import summarize_24h

    monkeypatch.setenv("CRYPTO_ADVERSARIAL_ENABLED", "1")
    out = await summarize_24h(db)
    assert out["enabled"] is True
    assert out["decisions_24h"] == 0
    assert out["last_decision_at"] is None


@pytest.mark.asyncio
async def test_summarize_24h_aggregates(db, monkeypatch):
    from services.adversarial_monitor import summarize_24h, COLLECTION

    monkeypatch.setenv("CRYPTO_ADVERSARIAL_ENABLED", "1")
    monkeypatch.setenv("CRYPTO_ADVERSARIAL_PHASE", "shadow")
    seeded = [
        _row(decision="LONG", bull_conf=0.80, bear_conf=0.30,
             edge_gap=0.50, age_minutes=10,
             winner="bull", loser="bear", final_r=1.5),
        _row(decision="SHORT_OR_AVOID", bull_conf=0.40, bear_conf=0.75,
             edge_gap=-0.35, age_minutes=30,
             winner="bear", loser="bull", final_r=-0.5),
        _row(decision="NO_TRADE", bull_conf=0.55, bear_conf=0.52,
             edge_gap=0.03, age_minutes=60),
        _row(decision="LONG", bull_conf=0.70, bear_conf=0.35,
             edge_gap=0.35, age_minutes=90,
             winner="bull", loser="bear", final_r=0.8),
    ]
    await db[COLLECTION].insert_many(seeded)

    out = await summarize_24h(db)
    assert out["decisions_24h"] == 4
    assert out["closed_24h"] == 3
    assert out["decision_counts"] == {
        "LONG": 2, "SHORT_OR_AVOID": 1, "NO_TRADE": 1,
    }
    assert out["wins"] == {"bull": 2, "bear": 1, "neutral": 0}
    assert out["bull_win_rate"] == pytest.approx(2 / 3, abs=0.01)
    assert out["bear_win_rate"] == pytest.approx(1 / 3, abs=0.01)
    # Edge gap avg = (0.50 - 0.35 + 0.03 + 0.35) / 4 = 0.1325
    assert out["avg_edge_gap"] == pytest.approx(0.1325, abs=0.001)
    # Bull conf avg = (0.80 + 0.40 + 0.55 + 0.70) / 4 = 0.6125
    assert out["avg_bull_confidence"] == pytest.approx(0.6125, abs=0.001)
    # Bear conf avg = (0.30 + 0.75 + 0.52 + 0.35) / 4 = 0.48
    assert out["avg_bear_confidence"] == pytest.approx(0.48, abs=0.001)
    assert out["last_decision_at"] is not None


@pytest.mark.asyncio
async def test_summarize_24h_win_rate_none_until_closed_row(db, monkeypatch):
    from services.adversarial_monitor import summarize_24h, COLLECTION

    monkeypatch.setenv("CRYPTO_ADVERSARIAL_ENABLED", "1")
    # All rows still open (no winner field).
    for i in range(5):
        await db[COLLECTION].insert_one(
            _row(symbol=f"S{i}", age_minutes=5 + i),
        )
    out = await summarize_24h(db)
    assert out["decisions_24h"] == 5
    assert out["closed_24h"] == 0
    assert out["bull_win_rate"] is None
    assert out["bear_win_rate"] is None


@pytest.mark.asyncio
async def test_summarize_24h_excludes_old_rows(db, monkeypatch):
    from services.adversarial_monitor import summarize_24h, COLLECTION

    monkeypatch.setenv("CRYPTO_ADVERSARIAL_ENABLED", "1")
    await db[COLLECTION].insert_one(_row(age_minutes=30))       # inside
    await db[COLLECTION].insert_one(_row(age_minutes=25 * 60))  # 25h → out
    out = await summarize_24h(db)
    assert out["decisions_24h"] == 1
