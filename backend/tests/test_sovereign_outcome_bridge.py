"""Sovereign Outcome Bridge — backend→sidecar inbox tests (2026-05-22).

Pins the writer half (``services/sovereign_outcome_bridge.py``)
and the drainer half (``sovereign/outcome_inbox_client.py``)
contract:

* Idempotent on ``trade_id`` (closer-retry safe).
* Tags every row with ``brain`` (multi-brain cross-contamination
  guard).
* Translates closer label tokens (``win/loss/flat``) into the
  LocalState contract (``1/-1/0``).
* Normalizes direction tokens (``up/long/buy`` → ``BUY``,
  ``down/short/sell`` → ``SELL``).
* Drainer returns ``[]`` cleanly when MONGO_URL is missing.
* Drained rows get ``drained=True`` + ``drained_at`` stamp so
  subsequent ticks don't re-pull them.
* ``paper_trade_closer`` calls ``enqueue_outcome`` after every
  successful close — real fills AND observation closes.
* ``sidecar.tick()`` drains the inbox BEFORE the contribution
  empty-payload check, so newly populated outcomes unlock the
  contribution emit on the same tick.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from services.sovereign_outcome_bridge import (
    COLLECTION,
    _action_token,
    _outcome_int,
    enqueue_outcome,
)


# ── Pure-fn pins ───────────────────────────────────────────────────


def test_outcome_int_translates_labels():
    assert _outcome_int("win") == 1
    assert _outcome_int("WIN") == 1
    assert _outcome_int("loss") == -1
    assert _outcome_int("flat") == 0
    assert _outcome_int("") == 0
    assert _outcome_int(None) == 0  # type: ignore[arg-type]


def test_action_token_normalizes_directions():
    for up in ("up", "UP", "long", "buy", "LONG"):
        assert _action_token(up) == "BUY"
    for down in ("down", "short", "sell", "SHORT"):
        assert _action_token(down) == "SELL"
    # Unknown defaults to BUY (caller bug guard).
    assert _action_token("") == "BUY"


# ── In-memory mongo stand-in ──────────────────────────────────────


class _Coll:
    def __init__(self):
        self.docs: list[dict] = []

    async def find_one(self, q=None, proj=None):
        q = q or {}
        for d in self.docs:
            if all(d.get(k) == v for k, v in q.items()):
                out = dict(d)
                if proj and proj.get("_id") == 0:
                    out.pop("_id", None)
                return out
        return None

    async def insert_one(self, doc):
        d = dict(doc)
        d.setdefault("_id", f"id-{len(self.docs) + 1}")
        self.docs.append(d)

        class _R:
            inserted_id = d["_id"]
        return _R()

    def find(self, q=None, proj=None):
        matched = [d for d in self.docs if _match(d, q or {})]
        return _Cursor(matched)

    async def update_many(self, q, upd):
        n = 0
        for d in self.docs:
            if _match(d, q):
                for k, v in upd.get("$set", {}).items():
                    d[k] = v
                n += 1

        class _R:
            matched_count = n
            modified_count = n
        return _R()

    async def count_documents(self, q):
        return sum(1 for d in self.docs if _match(d, q or {}))


class _Cursor:
    def __init__(self, docs):
        self.docs = list(docs)

    def sort(self, *a, **k):
        # Sort by resolved_at ASC by default
        self.docs = sorted(self.docs, key=lambda d: d.get("resolved_at") or "")
        return self

    def limit(self, n):
        self.docs = self.docs[: max(0, int(n))]
        return self

    def __aiter__(self):
        self._it = iter(self.docs)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration


def _match(d, q):
    for k, v in q.items():
        got = d.get(k)
        if isinstance(v, dict):
            if "$ne" in v and got == v["$ne"]:
                return False
            if "$in" in v and got not in v["$in"]:
                return False
        else:
            if got != v:
                return False
    return True


class _DB:
    def __init__(self):
        self._cs: dict[str, _Coll] = {}

    def __getitem__(self, name):
        return self._cs.setdefault(name, _Coll())


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ── enqueue_outcome ────────────────────────────────────────────────


def test_enqueue_outcome_inserts_row():
    db = _DB()
    out = _run(enqueue_outcome(
        db, brain="alpha", trade_id="t-1", symbol="BTC/USD",
        direction="long", confidence=0.62, outcome_label="win",
        notional=100.0,
    ))
    assert out["ok"] is True
    assert out["deduped"] is False
    rows = db[COLLECTION].docs
    assert len(rows) == 1
    r = rows[0]
    assert r["brain"] == "alpha"
    assert r["trade_id"] == "t-1"
    assert r["action"] == "BUY"
    assert r["outcome"] == 1
    assert r["outcome_label"] == "win"
    assert r["confidence"] == pytest.approx(0.62)
    assert r["notional"] == pytest.approx(100.0)
    assert r["drained"] is False


def test_enqueue_outcome_is_idempotent_on_trade_id_and_brain():
    db = _DB()
    _run(enqueue_outcome(
        db, brain="alpha", trade_id="t-dup", symbol="X",
        direction="up", confidence=0.5, outcome_label="loss",
    ))
    out = _run(enqueue_outcome(
        db, brain="alpha", trade_id="t-dup", symbol="X",
        direction="up", confidence=0.5, outcome_label="loss",
    ))
    assert out["deduped"] is True
    assert len(db[COLLECTION].docs) == 1


def test_enqueue_outcome_rejects_missing_trade_id():
    db = _DB()
    out = _run(enqueue_outcome(
        db, brain="alpha", trade_id="", symbol="X",
        direction="up", confidence=0.5, outcome_label="win",
    ))
    assert out["ok"] is False
    assert out["reason"] == "missing_trade_id"


def test_enqueue_outcome_translates_direction_and_label():
    db = _DB()
    _run(enqueue_outcome(
        db, brain="alpha", trade_id="t-2", symbol="ETH/USD",
        direction="down", confidence=0.55, outcome_label="loss",
    ))
    r = db[COLLECTION].docs[-1]
    assert r["action"] == "SELL"
    assert r["outcome"] == -1


# ── Drainer (sidecar-side client) ──────────────────────────────────


def test_drainer_returns_empty_when_mongo_unset(monkeypatch):
    monkeypatch.delenv("MONGO_URL", raising=False)
    monkeypatch.delenv("DB_NAME", raising=False)
    from sovereign import outcome_inbox_client as oic
    # Reset the cached db so the env miss is honored.
    oic._CACHED_DB = None
    rows = oic.drain_pending_for_brain_sync("alpha", limit=10)
    assert rows == []


def test_drainer_marks_rows_as_drained(monkeypatch):
    """Patch the drainer's _get_db so we can drive it against the
    in-memory mock without touching real Mongo."""
    from sovereign import outcome_inbox_client as oic
    db = _DB()
    # Seed 3 pending rows for alpha + 1 for camaro (must be ignored).
    for tid, brain, rsv in (("a1", "alpha", "2026-05-22T00:00:00"),
                            ("a2", "alpha", "2026-05-22T00:00:01"),
                            ("a3", "alpha", "2026-05-22T00:00:02"),
                            ("c1", "camaro", "2026-05-22T00:00:00")):
        _run(enqueue_outcome(
            db, brain=brain, trade_id=tid, symbol="X",
            direction="up", confidence=0.5, outcome_label="win",
        ))
        # Override resolved_at to test sort
        for r in db[COLLECTION].docs:
            if r["trade_id"] == tid:
                r["resolved_at"] = rsv

    monkeypatch.setattr(oic, "_get_db", lambda: db)
    rows = _run(oic.drain_pending_for_brain("alpha", limit=10))
    assert [r["trade_id"] for r in rows] == ["a1", "a2", "a3"]
    # All alpha rows now marked drained=True
    alphas = [r for r in db[COLLECTION].docs if r["brain"] == "alpha"]
    assert all(r["drained"] is True for r in alphas)
    # Camaro row untouched
    camaros = [r for r in db[COLLECTION].docs if r["brain"] == "camaro"]
    assert camaros[0]["drained"] is False
    # Second drain returns nothing
    rows2 = _run(oic.drain_pending_for_brain("alpha", limit=10))
    assert rows2 == []


# ── paper_trade_closer integration (static authority) ─────────────


def test_paper_trade_closer_calls_enqueue_outcome():
    """The closer's success branch must invoke ``enqueue_outcome``
    so resolved trades populate the inbox."""
    src = Path("/app/backend/services/paper_trade_closer.py").read_text(encoding="utf-8")
    assert "from services.sovereign_outcome_bridge import" in src
    assert "enqueue_outcome" in src
    # Live wired inside the success branch (after modified_count check)
    success_start = src.find("if res.modified_count:")
    assert success_start > 0
    success_end = src.find("# Sync paper_positions roster", success_start)
    success_block = src[success_start:success_end]
    assert "enqueue_outcome" in success_block
    assert 'brain="alpha"' in success_block


# ── sidecar tick wiring (static authority) ────────────────────────


def test_sidecar_tick_drains_outcome_inbox_before_contribution_check():
    """The drain must happen BEFORE the contribution-emit guard
    so newly drained outcomes unlock the post on the same tick."""
    src = Path("/app/backend/sovereign/sidecar.py").read_text(encoding="utf-8")
    tick_start = src.find("def tick(self) -> None:")
    next_def = src.find("\n    def ", tick_start + 10)
    tick_body = src[tick_start:next_def]

    drain_pos = tick_body.find("drain_pending_for_brain_sync")
    guard_pos = tick_body.find("if not recent:")
    assert drain_pos > 0, "tick must call drain_pending_for_brain_sync"
    assert drain_pos < guard_pos, (
        "drain must run BEFORE the empty-payload guard"
    )
    # Failures inside the drain are non-fatal.
    assert "outcome drain failed (non-fatal)" in tick_body
    # Drained rows are appended to LocalState via add_outcome.
    assert "self.state.add_outcome(" in tick_body
    # State is persisted after a successful drain.
    assert "self.state.save()" in tick_body
