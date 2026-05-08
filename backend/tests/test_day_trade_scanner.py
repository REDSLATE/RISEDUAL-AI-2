"""
Tests for ``services.day_trade_scanner``.

Critical invariants pinned here:

1. ``scan_universe`` is read-only — never writes to ``paper_trades``,
   ``crypto_paper_trades``, ``predictions``, or any collection.
2. ``rank_candidates`` is a deterministic pure function — same
   input → same output.
3. ``apply_gates`` blocks on min-score, non-directional, abandonment,
   already-holding, and pending-target.
4. ``run_scan`` writes EXACTLY ONE target row when a winner exists,
   ZERO when all candidates are blocked or universe is empty.
5. ``run_scan`` always writes a ``day_trade_scan_log`` entry — even
   when no winner is chosen.
6. Asset-class filter splits crypto vs equity correctly.
"""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone

import pytest


# ── In-memory fakes ────────────────────────────────────────────────


class _AsyncCursor:
    def __init__(self, rows):
        self._rows = list(rows)

    def limit(self, _n):
        return self

    async def to_list(self, length=None):  # noqa: ARG002
        return list(self._rows)


class _FakeAggCursor:
    def __init__(self, rows):
        self._rows = rows

    async def to_list(self, length=None):  # noqa: ARG002
        return list(self._rows)


def _doc_matches(doc, query):
    for k, v in query.items():
        if isinstance(v, dict):
            if "$gte" in v and (doc.get(k) is None or doc[k] < v["$gte"]):
                return False
            if "$lt" in v and (doc.get(k) is None or doc[k] >= v["$lt"]):
                return False
            if "$lte" in v and (doc.get(k) is None or doc[k] > v["$lte"]):
                return False
            if "$in" in v and doc.get(k) not in v["$in"]:
                return False
        else:
            if doc.get(k) != v:
                return False
    return True


class _FakeCollection:
    def __init__(self):
        self._docs: list[dict] = []

    async def insert_one(self, doc):
        self._docs.append(dict(doc))

    async def update_one(self, query, update, upsert=False):
        for d in self._docs:
            if _doc_matches(d, query):
                if "$set" in update:
                    d.update(update["$set"])
                if "$setOnInsert" in update:
                    pass  # exists already, $setOnInsert no-op
                return
        if upsert:
            new = {}
            if "$set" in update:
                new.update(update["$set"])
            if "$setOnInsert" in update:
                new.update(update["$setOnInsert"])
            self._docs.append(new)

    async def count_documents(self, query):
        return sum(1 for d in self._docs if _doc_matches(d, query))

    def find(self, query, projection=None):  # noqa: ARG002
        return _AsyncCursor([d for d in self._docs if _doc_matches(d, query)])

    def aggregate(self, pipeline):
        # Minimal pipeline runner sufficient for the scanner's read.
        rows = list(self._docs)
        for stage in pipeline:
            if "$match" in stage:
                q = stage["$match"]
                rows = [d for d in rows if _doc_matches(d, q)]
            elif "$sort" in stage:
                spec = stage["$sort"]
                for key, direction in reversed(list(spec.items())):
                    rows.sort(key=lambda d: d.get(key) or "", reverse=(direction == -1))
            elif "$group" in stage:
                g = stage["$group"]
                key = g["_id"].lstrip("$")
                seen: dict = {}
                for d in rows:
                    k = d.get(key)
                    if k in seen:
                        continue
                    grp = {"_id": k}
                    for out_key, spec in g.items():
                        if out_key == "_id":
                            continue
                        if "$first" in spec:
                            grp[out_key] = d.get(spec["$first"].lstrip("$"))
                    seen[k] = grp
                rows = list(seen.values())
        return _FakeAggCursor(rows)


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


def _seed_prediction(db, *, symbol, direction, confidence,
                     calibrated=None, minutes_ago=2):
    ts = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()
    db.predictions._docs.append({
        "feature": "paper_trading",
        "symbol": symbol,
        "direction": direction,
        "confidence": confidence,
        "calibrated_confidence": calibrated,
        "timestamp": ts,
        "prediction_id": f"pred_{symbol}_{minutes_ago}",
    })


# ── Phase 1: scan_universe is read-only ───────────────────────────


@pytest.mark.asyncio
async def test_scan_universe_reads_only_does_not_write(db):
    from services.day_trade_scanner import (
        TARGETS_COLLECTION, SCAN_LOG_COLLECTION, scan_universe,
    )
    _seed_prediction(db, symbol="AAPL", direction="UP", confidence=0.7)
    _seed_prediction(db, symbol="NVDA", direction="UP", confidence=0.65)

    out = await scan_universe(db, "equity")
    assert len(out) == 2
    # No write to any of these collections during scan.
    assert len(db["paper_trades"]._docs) == 0
    assert len(db["crypto_paper_trades"]._docs) == 0
    assert len(db[TARGETS_COLLECTION]._docs) == 0
    assert len(db[SCAN_LOG_COLLECTION]._docs) == 0


@pytest.mark.asyncio
async def test_scan_universe_filters_by_asset_class(db):
    from services.day_trade_scanner import scan_universe
    _seed_prediction(db, symbol="AAPL", direction="UP", confidence=0.7)
    _seed_prediction(db, symbol="BTC", direction="UP", confidence=0.65)
    _seed_prediction(db, symbol="ETH", direction="DOWN", confidence=0.4)

    eq = await scan_universe(db, "equity")
    cr = await scan_universe(db, "crypto")
    assert {c.symbol for c in eq} == {"AAPL"}
    assert {c.symbol for c in cr} == {"BTC", "ETH"}


@pytest.mark.asyncio
async def test_scan_universe_excludes_stale_predictions(db):
    from services.day_trade_scanner import scan_universe
    _seed_prediction(db, symbol="AAPL", direction="UP", confidence=0.7,
                     minutes_ago=60)
    out = await scan_universe(db, "equity")
    assert out == []


@pytest.mark.asyncio
async def test_scan_universe_uses_calibrated_when_present(db):
    from services.day_trade_scanner import scan_universe
    _seed_prediction(db, symbol="AAPL", direction="UP", confidence=0.55,
                     calibrated=0.92)
    out = await scan_universe(db, "equity")
    assert out[0].score == 0.92
    assert out[0].confidence_calibrated == 0.92


@pytest.mark.asyncio
async def test_scan_universe_directional_score_for_down_signal(db):
    """Without calibration, a DOWN prediction at raw 0.3 P(up) means
    P(short_wins) = 0.7 — should rank higher than a UP at 0.55."""
    from services.day_trade_scanner import scan_universe
    _seed_prediction(db, symbol="AAPL", direction="UP", confidence=0.55)
    _seed_prediction(db, symbol="NVDA", direction="DOWN", confidence=0.30)
    out = await scan_universe(db, "equity")
    by_sym = {c.symbol: c for c in out}
    assert by_sym["AAPL"].score == pytest.approx(0.55, abs=0.001)
    assert by_sym["NVDA"].score == pytest.approx(0.70, abs=0.001)


# ── Phase 2: rank is deterministic ────────────────────────────────


def test_rank_candidates_descending_score():
    from services.day_trade_scanner import ScanCandidate, rank_candidates
    c1 = ScanCandidate(symbol="AAPL", asset_class="equity", direction="UP",
                       score=0.7, confidence_raw=0.7, confidence_calibrated=None,
                       prediction_id="p1", prediction_at="t1")
    c2 = ScanCandidate(symbol="NVDA", asset_class="equity", direction="UP",
                       score=0.92, confidence_raw=0.92, confidence_calibrated=None,
                       prediction_id="p2", prediction_at="t2")
    c3 = ScanCandidate(symbol="TSLA", asset_class="equity", direction="UP",
                       score=0.55, confidence_raw=0.55, confidence_calibrated=None,
                       prediction_id="p3", prediction_at="t3")
    out = rank_candidates([c1, c2, c3])
    assert [c.symbol for c in out] == ["NVDA", "AAPL", "TSLA"]
    assert [c.rank for c in out] == [1, 2, 3]


def test_rank_candidates_alphabetical_tiebreak():
    from services.day_trade_scanner import ScanCandidate, rank_candidates
    a = ScanCandidate(symbol="ZZZZ", asset_class="equity", direction="UP",
                      score=0.7, confidence_raw=0.7, confidence_calibrated=None,
                      prediction_id="p1", prediction_at="t1")
    b = ScanCandidate(symbol="AAAA", asset_class="equity", direction="UP",
                      score=0.7, confidence_raw=0.7, confidence_calibrated=None,
                      prediction_id="p2", prediction_at="t2")
    out = rank_candidates([a, b])
    assert [c.symbol for c in out] == ["AAAA", "ZZZZ"]


def test_rank_candidates_pure_does_not_mutate_input_order():
    from services.day_trade_scanner import ScanCandidate, rank_candidates
    items = [
        ScanCandidate(symbol="X", asset_class="equity", direction="UP",
                      score=s, confidence_raw=s, confidence_calibrated=None,
                      prediction_id="p", prediction_at="t")
        for s in (0.3, 0.7, 0.5)
    ]
    snapshot = [copy.deepcopy(c) for c in items]
    _ = rank_candidates(items)
    # Source list order preserved on the originals (sorting returns
    # a NEW list); only the returned list is sorted.
    assert [c.score for c in items] == [c.score for c in snapshot]


# ── Phase 3: gates ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_gate_blocks_below_min_score(db):
    from services.day_trade_scanner import ScanCandidate, apply_gates
    c = ScanCandidate(symbol="AAPL", asset_class="equity", direction="UP",
                      score=0.40, confidence_raw=0.40,
                      confidence_calibrated=None, prediction_id="p",
                      prediction_at="t")
    passed, blocker, _ = await apply_gates(db, c)
    assert not passed
    assert blocker == "below_min_score"


@pytest.mark.asyncio
async def test_gate_blocks_neutral_direction(db):
    from services.day_trade_scanner import ScanCandidate, apply_gates
    c = ScanCandidate(symbol="AAPL", asset_class="equity", direction="HOLD",
                      score=0.92, confidence_raw=0.92,
                      confidence_calibrated=None, prediction_id="p",
                      prediction_at="t")
    passed, blocker, _ = await apply_gates(db, c)
    assert not passed
    assert blocker == "non_directional_prediction"


@pytest.mark.asyncio
async def test_gate_blocks_when_already_holding(db):
    from services.day_trade_scanner import ScanCandidate, apply_gates
    db["paper_trades"]._docs.append({
        "ticker": "AAPL", "status": "open", "trade_id": "x",
    })
    c = ScanCandidate(symbol="AAPL", asset_class="equity", direction="UP",
                      score=0.92, confidence_raw=0.92,
                      confidence_calibrated=None, prediction_id="p",
                      prediction_at="t")
    passed, blocker, _ = await apply_gates(db, c)
    assert not passed
    assert blocker == "already_holding"


@pytest.mark.asyncio
async def test_gate_blocks_when_target_pending(db):
    from services.day_trade_scanner import (
        TARGETS_COLLECTION, ScanCandidate, apply_gates,
    )
    db[TARGETS_COLLECTION]._docs.append({
        "symbol": "AAPL", "status": "pending", "target_id": "t1",
    })
    c = ScanCandidate(symbol="AAPL", asset_class="equity", direction="UP",
                      score=0.92, confidence_raw=0.92,
                      confidence_calibrated=None, prediction_id="p",
                      prediction_at="t")
    passed, blocker, _ = await apply_gates(db, c)
    assert not passed
    assert blocker == "target_pending"


@pytest.mark.asyncio
async def test_gate_passes_clean_candidate(db):
    from services.day_trade_scanner import ScanCandidate, apply_gates
    c = ScanCandidate(symbol="AAPL", asset_class="equity", direction="UP",
                      score=0.92, confidence_raw=0.92,
                      confidence_calibrated=None, prediction_id="p",
                      prediction_at="t")
    passed, blocker, _ = await apply_gates(db, c)
    assert passed
    assert blocker is None


# ── Phase 4: run_scan top-1 invariant ─────────────────────────────


@pytest.mark.asyncio
async def test_run_scan_picks_top_1_only(db):
    """Three valid candidates → only ONE target row written."""
    from services.day_trade_scanner import (
        TARGETS_COLLECTION, SCAN_LOG_COLLECTION, run_scan,
    )
    _seed_prediction(db, symbol="AAPL", direction="UP", confidence=0.92)
    _seed_prediction(db, symbol="NVDA", direction="UP", confidence=0.85)
    _seed_prediction(db, symbol="TSLA", direction="UP", confidence=0.78)

    out = await run_scan(db, "equity")
    assert out.chosen is not None
    assert out.chosen.symbol == "AAPL"  # highest score
    assert out.total_scanned == 3
    # Exactly one target written.
    assert len(db[TARGETS_COLLECTION]._docs) == 1
    assert db[TARGETS_COLLECTION]._docs[0]["symbol"] == "AAPL"
    # Audit log written.
    assert len(db[SCAN_LOG_COLLECTION]._docs) == 1
    log = db[SCAN_LOG_COLLECTION]._docs[0]
    assert log["chosen"]["symbol"] == "AAPL"
    # Every candidate's gate verdict is recorded — not just the chosen.
    assert len(log["candidates"]) == 3
    for cand in log["candidates"]:
        assert "gate_passed" in cand


@pytest.mark.asyncio
async def test_run_scan_writes_target_with_eod_timer(db):
    from services.day_trade_scanner import TARGETS_COLLECTION, run_scan
    _seed_prediction(db, symbol="AAPL", direction="UP", confidence=0.92)
    await run_scan(db, "equity")
    target = db[TARGETS_COLLECTION]._docs[0]
    assert "max_hold_until" in target
    assert isinstance(target["max_hold_until"], datetime)
    # 21:00 UTC of today or tomorrow.
    assert target["max_hold_until"].hour == 21
    assert target["max_hold_until"].minute == 0
    assert target["status"] == "pending"


@pytest.mark.asyncio
async def test_run_scan_no_winner_when_all_blocked(db):
    """All candidates below min-score → no target written, but
    scan log still fires."""
    from services.day_trade_scanner import (
        TARGETS_COLLECTION, SCAN_LOG_COLLECTION, run_scan,
    )
    _seed_prediction(db, symbol="AAPL", direction="UP", confidence=0.40)
    _seed_prediction(db, symbol="NVDA", direction="UP", confidence=0.45)

    out = await run_scan(db, "equity")
    assert out.chosen is None
    assert out.blocked_count == 2
    assert out.total_scanned == 2
    assert len(db[TARGETS_COLLECTION]._docs) == 0
    assert len(db[SCAN_LOG_COLLECTION]._docs) == 1


@pytest.mark.asyncio
async def test_run_scan_empty_universe(db):
    from services.day_trade_scanner import (
        TARGETS_COLLECTION, SCAN_LOG_COLLECTION, run_scan,
    )
    out = await run_scan(db, "equity")
    assert out.chosen is None
    assert out.total_scanned == 0
    assert out.blocked_count == 0
    assert len(db[TARGETS_COLLECTION]._docs) == 0
    # Scan log STILL written (audit trail every tick).
    assert len(db[SCAN_LOG_COLLECTION]._docs) == 1


@pytest.mark.asyncio
async def test_run_scan_never_writes_to_paper_trades(db):
    """Critical invariant: scanner stays out of the live trade
    collections. ALL writes go through the executor module."""
    from services.day_trade_scanner import run_scan
    _seed_prediction(db, symbol="AAPL", direction="UP", confidence=0.92)
    _seed_prediction(db, symbol="BTC", direction="UP", confidence=0.85)
    await run_scan(db, "equity")
    await run_scan(db, "crypto")
    assert len(db["paper_trades"]._docs) == 0
    assert len(db["crypto_paper_trades"]._docs) == 0
