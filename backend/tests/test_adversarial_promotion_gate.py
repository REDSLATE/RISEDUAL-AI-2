"""
Tests for ``services.adversarial_promotion_gate.compute_promotion_status``.

Pins the contract the admin Terminal "Ready to promote?" pill consumes:

1. ``db=None`` returns the empty envelope (rendererable on cold-start).
2. Empty collection → all counts 0, ``trustworthy=False``,
   ``ready_to_promote=False``, blocker mentions the row gap.
3. Below the trustworthy floor (default 20 closed rows) → trustworthy
   stays False; ``rows_to_trustworthy`` counts down.
4. Hit the trustworthy floor with low correct-rate → trustworthy True
   but ``ready_to_promote=False``, blocker explains the rate gap.
5. Hit row floor + rate floor → ``ready_to_promote=True``,
   ``promote_env_line=CRYPTO_ADVERSARIAL_PHASE=risk_only``.
6. Phase=risk_only with >= 50 rows + 58% rate → next pill targets veto.
7. Phase=full → terminal, ``next_transition=None``,
   ``ready_to_promote=False``, ``promote_env_line=None``.
8. NO_TRADE rows do NOT count toward commander_correct (only LONG+bull
   and SHORT_OR_AVOID+bear do — pinned per the gate's docstring).
9. Bull/Bear lifetime spread is computed across ALL closed rows
   regardless of which side Commander picked.
10. Env-overridable thresholds — flipping ``ADV_PROMOTE_S_TO_R_ROWS``
    moves the gate.
"""
from __future__ import annotations

import pytest


class _FakeCursor:
    def __init__(self, rows):
        self._rows = list(rows)


def _doc_matches(doc: dict, query: dict) -> bool:
    """Tiny in-memory matcher for the queries this module issues."""
    for key, val in query.items():
        if key == "$or":
            if not any(_doc_matches(doc, sub) for sub in val):
                return False
            continue
        if isinstance(val, dict) and "$in" in val:
            if doc.get(key) not in val["$in"]:
                return False
            continue
        if doc.get(key) != val:
            return False
    return True


class _FakeCollection:
    def __init__(self):
        self._docs: list[dict] = []

    async def insert_many(self, docs):
        self._docs.extend([dict(d) for d in docs])

    async def insert_one(self, doc):
        self._docs.append(dict(doc))

    async def count_documents(self, query):
        return sum(1 for d in self._docs if _doc_matches(d, query))


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


def _row(*, decision: str, winner: str | None = None) -> dict:
    """Minimal row that satisfies the gate's queries.

    ``winner`` may be None (still-open) or one of bull/bear/neutral.
    """
    out = {"decision": decision}
    if winner is not None:
        out["winner"] = winner
    return out


async def _seed(db, rows):
    from services.adversarial_promotion_gate import CollectionName

    await db[CollectionName].insert_many(rows)


@pytest.mark.asyncio
async def test_none_db_returns_empty_envelope(monkeypatch):
    from services.adversarial_promotion_gate import compute_promotion_status

    monkeypatch.delenv("CRYPTO_ADVERSARIAL_ENABLED", raising=False)
    monkeypatch.setenv("CRYPTO_ADVERSARIAL_PHASE", "shadow")
    out = await compute_promotion_status(None)
    assert out["closed_lifetime"] == 0
    assert out["trustworthy"] is False
    assert out["ready_to_promote"] is False
    assert out["next_transition"] == "shadow_to_risk_only"
    assert out["promote_env_line"] == "CRYPTO_ADVERSARIAL_PHASE=risk_only"
    assert "thresholds" in out


@pytest.mark.asyncio
async def test_empty_collection(db, monkeypatch):
    from services.adversarial_promotion_gate import compute_promotion_status

    monkeypatch.setenv("CRYPTO_ADVERSARIAL_ENABLED", "1")
    monkeypatch.setenv("CRYPTO_ADVERSARIAL_PHASE", "shadow")
    out = await compute_promotion_status(db)
    assert out["closed_lifetime"] == 0
    assert out["commander_correct_rate"] is None
    assert out["spread_lifetime_pp"] is None
    assert out["trustworthy"] is False
    assert out["rows_to_trustworthy"] == 20
    assert out["ready_to_promote"] is False
    assert "more closed rows" in (out["blocker"] or "")


@pytest.mark.asyncio
async def test_below_trustworthy_threshold(db, monkeypatch):
    from services.adversarial_promotion_gate import compute_promotion_status

    monkeypatch.setenv("CRYPTO_ADVERSARIAL_PHASE", "shadow")
    rows = [_row(decision="LONG", winner="bull") for _ in range(10)]
    await _seed(db, rows)

    out = await compute_promotion_status(db)
    assert out["closed_lifetime"] == 10
    assert out["trustworthy"] is False
    assert out["rows_to_trustworthy"] == 10
    assert out["ready_to_promote"] is False
    assert "more closed rows" in (out["blocker"] or "")


@pytest.mark.asyncio
async def test_trustworthy_but_low_rate(db, monkeypatch):
    """20 closed rows with only 40% Commander-correct — trustworthy
    crosses, but readiness blocks on the rate floor."""
    from services.adversarial_promotion_gate import compute_promotion_status

    monkeypatch.setenv("CRYPTO_ADVERSARIAL_PHASE", "shadow")
    # 8 correct (LONG+bull) + 12 wrong (LONG+bear) = 40% correct rate
    rows = (
        [_row(decision="LONG", winner="bull") for _ in range(8)]
        + [_row(decision="LONG", winner="bear") for _ in range(12)]
    )
    await _seed(db, rows)

    out = await compute_promotion_status(db)
    assert out["closed_lifetime"] == 20
    assert out["trustworthy"] is True
    assert out["commander_correct_rate"] == pytest.approx(0.40, abs=0.01)
    assert out["ready_to_promote"] is False
    assert "below" in (out["blocker"] or "")


@pytest.mark.asyncio
async def test_shadow_to_risk_only_ready(db, monkeypatch):
    from services.adversarial_promotion_gate import compute_promotion_status

    monkeypatch.setenv("CRYPTO_ADVERSARIAL_PHASE", "shadow")
    # 12 correct + 8 wrong = 60% correct rate, ≥ 55% floor + 20 rows.
    rows = (
        [_row(decision="LONG", winner="bull") for _ in range(12)]
        + [_row(decision="LONG", winner="bear") for _ in range(8)]
    )
    await _seed(db, rows)

    out = await compute_promotion_status(db)
    assert out["closed_lifetime"] == 20
    assert out["commander_correct_rate"] == pytest.approx(0.60, abs=0.01)
    assert out["ready_to_promote"] is True
    assert out["blocker"] is None
    assert out["next_transition"] == "shadow_to_risk_only"
    assert out["promote_env_line"] == "CRYPTO_ADVERSARIAL_PHASE=risk_only"


@pytest.mark.asyncio
async def test_risk_only_targets_veto(db, monkeypatch):
    from services.adversarial_promotion_gate import compute_promotion_status

    monkeypatch.setenv("CRYPTO_ADVERSARIAL_PHASE", "risk_only")
    # 30 correct (LONG+bull) + 20 wrong (LONG+bear) = 60% rate, ≥ 58% floor.
    rows = (
        [_row(decision="LONG", winner="bull") for _ in range(30)]
        + [_row(decision="LONG", winner="bear") for _ in range(20)]
    )
    await _seed(db, rows)

    out = await compute_promotion_status(db)
    assert out["current_phase"] == "risk_only"
    assert out["next_phase"] == "veto"
    assert out["next_transition"] == "risk_only_to_veto"
    assert out["closed_lifetime"] == 50
    assert out["ready_to_promote"] is True
    assert out["promote_env_line"] == "CRYPTO_ADVERSARIAL_PHASE=veto"


@pytest.mark.asyncio
async def test_full_phase_terminal(db, monkeypatch):
    from services.adversarial_promotion_gate import compute_promotion_status

    monkeypatch.setenv("CRYPTO_ADVERSARIAL_PHASE", "full")
    # Even with a beautiful 80% correct rate at full, no further phase.
    rows = (
        [_row(decision="LONG", winner="bull") for _ in range(80)]
        + [_row(decision="LONG", winner="bear") for _ in range(20)]
    )
    await _seed(db, rows)

    out = await compute_promotion_status(db)
    assert out["current_phase"] == "full"
    assert out["next_phase"] is None
    assert out["next_transition"] is None
    assert out["ready_to_promote"] is False
    assert out["promote_env_line"] is None
    assert out["blocker"] == "already at terminal phase 'full'"


@pytest.mark.asyncio
async def test_no_trade_rows_excluded_from_commander_correct(db, monkeypatch):
    """NO_TRADE in shadow phase should never count toward Commander
    correct/wrong — the trade fired anyway and the credit goes to
    Bull/Bear, not to the Commander's vote."""
    from services.adversarial_promotion_gate import compute_promotion_status

    monkeypatch.setenv("CRYPTO_ADVERSARIAL_PHASE", "shadow")
    # 12 correct + 8 wrong = 60% rate over 20 rows.
    # Plus 50 NO_TRADE rows that mustn't move commander_correct_rate.
    rows = (
        [_row(decision="LONG", winner="bull") for _ in range(12)]
        + [_row(decision="LONG", winner="bear") for _ in range(8)]
        + [_row(decision="NO_TRADE", winner="bull") for _ in range(50)]
    )
    await _seed(db, rows)

    out = await compute_promotion_status(db)
    assert out["closed_lifetime"] == 70
    # commander_correct stays 12 — NO_TRADE+bull does NOT count.
    assert out["commander_correct"] == 12
    assert out["commander_correct_rate"] == pytest.approx(12 / 70, abs=0.01)
    # Below the 55% floor now, so promotion blocks despite the row count.
    assert out["ready_to_promote"] is False


@pytest.mark.asyncio
async def test_lifetime_spread_aggregates_all_closed_rows(db, monkeypatch):
    from services.adversarial_promotion_gate import compute_promotion_status

    monkeypatch.setenv("CRYPTO_ADVERSARIAL_PHASE", "shadow")
    rows = (
        [_row(decision="LONG", winner="bull") for _ in range(15)]
        + [_row(decision="SHORT_OR_AVOID", winner="bear") for _ in range(10)]
        + [_row(decision="LONG", winner="bear") for _ in range(5)]
    )
    await _seed(db, rows)

    out = await compute_promotion_status(db)
    # Total closed = 30; Bull wins = 15; Bear wins = 15.
    assert out["closed_lifetime"] == 30
    assert out["bull_win_rate_lifetime"] == pytest.approx(0.50, abs=0.01)
    assert out["bear_win_rate_lifetime"] == pytest.approx(0.50, abs=0.01)
    assert out["spread_lifetime_pp"] == pytest.approx(0.0, abs=0.1)


@pytest.mark.asyncio
async def test_env_overrides_thresholds(db, monkeypatch):
    """Flipping ``ADV_PROMOTE_S_TO_R_ROWS`` to 5 should let 5 closed
    rows clear the gate (provided the rate floor is met)."""
    from services.adversarial_promotion_gate import compute_promotion_status

    monkeypatch.setenv("CRYPTO_ADVERSARIAL_PHASE", "shadow")
    monkeypatch.setenv("ADV_PROMOTE_S_TO_R_ROWS", "5")
    monkeypatch.setenv("ADV_TRUSTWORTHY_MIN_CLOSED", "5")
    rows = (
        [_row(decision="LONG", winner="bull") for _ in range(3)]
        + [_row(decision="LONG", winner="bear") for _ in range(2)]
    )
    await _seed(db, rows)

    # Reload TRUSTWORTHY_MIN_CLOSED — it's read at import time, so we
    # reload the module to pick up the env override.
    import importlib
    import services.adversarial_promotion_gate as mod
    importlib.reload(mod)

    out = await mod.compute_promotion_status(db)
    assert out["closed_lifetime"] == 5
    assert out["trustworthy"] is True
    assert out["commander_correct_rate"] == pytest.approx(0.60, abs=0.01)
    assert out["ready_to_promote"] is True
