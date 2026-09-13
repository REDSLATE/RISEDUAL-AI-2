"""Round-Trip Proof — Alpha's complete long lifecycle end-to-end.

Operator spec (P1-B follow-up):

    Long intent → Public submit → ACK → fill → position tracking →
    SELL_TO_CLOSE → ACK → fill → reconcile position → outcome → realized_r

Every stage must produce a *real* artifact. This proof fails if any
stage is merely inferred:

    Stage 1: OPEN intent → equity_live_trades row exists with
             status="open" AND a non-empty broker_order_id.
    Stage 2: Broker ACK captured via broker_comparison_service or
             watchdog (existing infra) — verified by row.broker_order_id
             being carried forward, not fabricated.
    Stage 3: Fill price recorded (entry_price > 0 on the row).
    Stage 4: Position tracking observed — peak_price / trough_price
             updated by track_open_excursions on a mid-trade tick.
    Stage 5: SELL_TO_CLOSE intent → same row transitions to
             status="closed", close_order_id set, close_filled_qty
             equal to entry qty (no residual).
    Stage 6: Exit broker order ID must differ from entry order ID.
    Stage 7: resolve_closed_outcomes runs → alpha_outcomes row
             upserted with realized_r AND entry_broker_order_id AND
             exit_broker_order_id AND correct direction linkage.
    Stage 8: realized_r math verified against the fill prices — a
             +$10 move on a $5 stop-risk long returns realized_r=2.0.

The test uses in-memory Mongo-fakes so no real broker is touched.
Every assertion is explicit — no stage may rely on implicit behavior.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.alpha_fill_writer import (
    resolve_closed_outcomes,
    track_open_excursions,
)
from services.public_equity_live_executor import maybe_route_live


# ── Fakes: motor-like async collections ────────────────────────────


class _AsyncCursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def __aiter__(self):
        self._i = 0
        return self

    async def __anext__(self):
        if self._i >= len(self._docs):
            raise StopAsyncIteration
        d = self._docs[self._i]
        self._i += 1
        return d


class _FakeColl:
    def __init__(self):
        self.docs: list[dict] = []

    def _matches(self, doc, filter_):
        for k, v in (filter_ or {}).items():
            if isinstance(v, dict):
                # Simple ops we care about: $ne, $regex, $exists, $gte
                if "$ne" in v:
                    if doc.get(k) == v["$ne"]:
                        return False
                    continue
                if "$regex" in v:
                    if not re.search(v["$regex"], str(doc.get(k) or "")):
                        return False
                    continue
                if "$exists" in v:
                    exists = k in doc
                    if bool(v["$exists"]) != exists:
                        return False
                    continue
                if "$gte" in v:
                    if doc.get(k) is None or doc.get(k) < v["$gte"]:
                        return False
                    continue
                if "$lte" in v:
                    if doc.get(k) is None or doc.get(k) > v["$lte"]:
                        return False
                    continue
                # unknown op — fail closed
                return False
            elif doc.get(k) != v:
                return False
        return True

    async def find_one(self, filter_=None, _proj=None, **_kw):
        for d in self.docs:
            if self._matches(d, filter_):
                return dict(d)
        return None

    def find(self, filter_=None, _proj=None, **_kw):
        matches = [dict(d) for d in self.docs if self._matches(d, filter_)]
        return _AsyncCursor(matches)

    async def insert_one(self, doc):
        d = dict(doc)
        d.setdefault("_id", f"id-{len(self.docs) + 1}")
        self.docs.append(d)
        return MagicMock(inserted_id=d["_id"])

    async def update_one(self, filter_, update, upsert=False, **_kw):
        target = None
        for d in self.docs:
            if self._matches(d, filter_):
                target = d
                break
        if target is None:
            if upsert:
                new_doc = dict(filter_)
                new_doc.update(update.get("$set", {}))
                new_doc.setdefault("_id", f"id-{len(self.docs) + 1}")
                self.docs.append(new_doc)
                return MagicMock(matched_count=0, modified_count=0, upserted_id=new_doc["_id"])
            return MagicMock(matched_count=0, modified_count=0)
        for k, v in (update.get("$set") or {}).items():
            target[k] = v
        for k in (update.get("$unset") or {}).keys():
            target.pop(k, None)
        return MagicMock(matched_count=1, modified_count=1)

    async def count_documents(self, filter_=None):
        return sum(1 for d in self.docs if self._matches(d, filter_))


class _FakeDB:
    def __init__(self):
        self.broker_connections = _FakeColl()
        self.equity_live_trades = _FakeColl()
        self.intent_skip_log = _FakeColl()
        self.alpha_outcomes = _FakeColl()

    def __getitem__(self, k):
        return getattr(self, k)


def _make_broker_client(*, entry_order_id, exit_order_id, positions_before_close):
    """Broker fake whose place_order alternates between entry and exit
    responses. The position response is programmable so we can simulate
    the "position exists at close time" branch.

    Note: on close, the executor probes broker positions TWICE — once
    in the early classifier probe, once in the main close-branch
    reconciliation. Both must return the open long, so we use
    ``return_value`` (stable) instead of ``side_effect`` (one-shot).
    """
    c = MagicMock()
    c.get_account.return_value = {
        "id": "ACCT", "cash": 1000.0, "buying_power": 1000.0, "equity": 1000.0,
    }
    c.get_positions.return_value = list(positions_before_close)
    c.place_order.side_effect = [
        {"id": entry_order_id, "status": "filled", "filled_qty": 0.125,
         "fillPrice": 200.0},
        {"id": exit_order_id, "status": "filled", "filled_qty": 0.125,
         "fillPrice": 210.0},
    ]
    return c


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("RISEDUAL_PUBLIC_LIVE_EXEC", "1")
    monkeypatch.setenv("PUBLIC_LIVE_RTH_ONLY", "0")
    monkeypatch.setenv("PUBLIC_LIVE_CONFIDENCE_FLOOR", "0.0")
    monkeypatch.setenv("PUBLIC_LIVE_NOTIONAL_USD", "25")
    monkeypatch.delenv("PUBLIC_LIVE_SYMBOLS", raising=False)
    monkeypatch.setenv("PUBLIC_MAX_INTRADAY_MOVE_PCT", "0")
    yield


# ── The proof ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_alpha_long_round_trip_proof():
    """Execute all 8 stages of an Alpha long round trip and verify
    every artifact links back through broker order IDs."""
    db = _FakeDB()
    db.broker_connections.docs.append({
        "broker_id": "public", "user_id": "op",
        "api_key": "sk", "api_secret": "acct-1",
        "status": "connected",
    })

    ENTRY_ORDER_ID = "PUB-ENTRY-42"
    EXIT_ORDER_ID = "PUB-EXIT-77"
    SETUP_ID = "setup-alpha-XYZ-2026-02"

    client = _make_broker_client(
        entry_order_id=ENTRY_ORDER_ID,
        exit_order_id=EXIT_ORDER_ID,
        positions_before_close=[
            {"symbol": "AAPL", "qty": 0.125, "side": "long"},
        ],
    )

    # Alpha's intent shape — carries setup_id so the outcome resolver
    # can join back to alpha_outcomes.
    entry_intent = {
        "symbol": "AAPL",
        "direction": "BUY",
        "confidence": 0.80,
        "strategy_id": "alpha_daytrader:momentum_breakout",
        "source_signal": "alpha_daytrader:v1",
        "prediction_id": "pred-round-trip-1",
        "alpha_daytrader": {
            "setup_id": SETUP_ID,
            "confirmation_price": 200.0,
            "target_price": 210.0,
            "stop_price": 195.0,
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    }

    exit_intent = {
        "symbol": "AAPL",
        "direction": "SELL",
        "intent_action": "SELL_TO_CLOSE",   # explicit exit — never opens a short
        "exit_only": True,                   # hard guard against reversal
        "confidence": 0.80,
        "strategy_id": "alpha_daytrader:exit_monitor",
        "source_signal": "alpha_daytrader:exit_monitor:v1",
        "prediction_id": "pred-round-trip-1-exit",
        "alpha_daytrader": {
            "setup_id": SETUP_ID,
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    }

    # ── Stage 1-3: OPEN intent → row + broker_order_id + fill price ──
    with patch(
        "services.public_equity_live_executor._fetch_mark_price",
        new=AsyncMock(return_value=200.0),
    ), patch(
        "services.public_equity_live_executor._public_client",
        return_value=client,
    ):
        open_row = await maybe_route_live(db, intent=entry_intent)

    assert open_row is not None, "Stage 1 failed: no row returned from open intent"
    assert open_row["status"] == "open"
    assert open_row["broker_order_id"] == ENTRY_ORDER_ID, (
        "Stage 2 failed: entry broker_order_id did not propagate from Public"
    )
    assert open_row["entry_price"] == 200.0, "Stage 3 failed: no fill price recorded"
    assert open_row["direction"] == "LONG"

    # Mongo has exactly one row with the same broker_order_id and the
    # correct setup linkage.
    persisted = [d for d in db.equity_live_trades.docs
                 if d["broker_order_id"] == ENTRY_ORDER_ID]
    assert len(persisted) == 1, "Stage 1 failed: entry row not persisted"
    row = persisted[0]
    assert (row.get("alpha_daytrader") or {}).get("setup_id") == SETUP_ID

    # Pre-create the alpha_outcomes row (upsert=False in the resolver
    # matches Alpha's actual pipeline where setups are recorded upstream).
    await db.alpha_outcomes.insert_one({
        "setup_id": SETUP_ID,
        "symbol": "AAPL",
        "direction": "LONG",
        "created_at": datetime.now(timezone.utc),
    })

    # Row needs a stop_price for realized_r math to normalize by risk.
    # In production this is stamped by alpha_day_trader on setup fire;
    # in this proof we set it directly to isolate the lifecycle wiring.
    row["stop_price"] = 195.0

    # ── Stage 4: position tracking — track_open_excursions bumps peak/trough
    async def _quote_210():
        return 210.0
    with patch("services.alpha_fill_writer._quote",
               new=AsyncMock(return_value=210.0)):
        tracking = await track_open_excursions(db)
    assert tracking["updated"] == 1, "Stage 4 failed: excursion not updated"
    assert row["peak_price"] == 210.0, "Stage 4 failed: peak not recorded"

    # ── Stage 5-6: SELL_TO_CLOSE intent → row closes with exit order id
    with patch(
        "services.public_equity_live_executor._fetch_mark_price",
        new=AsyncMock(return_value=210.0),
    ), patch(
        "services.public_equity_live_executor._public_client",
        return_value=client,
    ):
        close_out = await maybe_route_live(db, intent=exit_intent)

    assert close_out is not None, "Stage 5 failed: close intent returned None"
    assert close_out["status"] == "closed", "Stage 5 failed: not fully closed"
    assert close_out["intent_kind"] == "close_long"
    assert close_out["broker_order_id"] == EXIT_ORDER_ID, (
        "Stage 6 failed: exit broker_order_id did not propagate"
    )
    assert close_out["broker_order_id"] != ENTRY_ORDER_ID, (
        "Stage 6 failed: exit and entry share the same broker order id — "
        "they must be distinct or the audit cannot join them"
    )
    assert close_out["filled_qty"] == 0.125
    assert close_out["remaining_qty"] == 0.0

    # Mongo row now reflects both order IDs — the audit-join foundation.
    row = db.equity_live_trades.docs[0]
    assert row["broker_order_id"] == ENTRY_ORDER_ID
    assert row["close_order_id"] == EXIT_ORDER_ID
    assert row["status"] == "closed"

    # ── Stage 7-8: resolve_closed_outcomes → outcome row with realized_r
    resolved = await resolve_closed_outcomes(db)
    assert resolved["resolved"] == 1, (
        "Stage 7 failed: outcome resolver did not run to completion"
    )

    outcome_docs = [d for d in db.alpha_outcomes.docs
                    if d["setup_id"] == SETUP_ID]
    assert len(outcome_docs) == 1, "Stage 7 failed: outcome row missing"
    outcome = outcome_docs[0]

    # ── The audit-linkage assertions — the whole point of the proof ──
    assert outcome["entry_broker_order_id"] == ENTRY_ORDER_ID, (
        "Stage 7 failed: outcome row does not link to entry order"
    )
    assert outcome["exit_broker_order_id"] == EXIT_ORDER_ID, (
        "Stage 7 failed: outcome row does not link to exit order"
    )
    assert outcome["direction"] == "LONG"
    assert outcome["entry_fill_price"] == 200.0
    assert outcome["exit_fill_price"] == 210.0

    # ── Stage 8: realized_r math — long +$10 with $5 stop distance = 2.0R
    assert outcome["realized_r"] == 2.0, (
        f"Stage 8 failed: realized_r math wrong (expected 2.0, got "
        f"{outcome['realized_r']})"
    )
    # Dollar PnL sanity — 0.125 sh × $10 = $1.25
    assert outcome["realized_pnl_usd"] == 1.25


@pytest.mark.asyncio
async def test_round_trip_refuses_when_exit_before_open():
    """Symmetric guard: a SELL_TO_CLOSE with exit_only=True against
    nothing must never sneak through as an open_short. The router
    guarantees this at the classification layer; this test locks the
    behavior end-to-end at the executor level."""
    db = _FakeDB()
    db.broker_connections.docs.append({
        "broker_id": "public", "user_id": "op",
        "api_key": "sk", "api_secret": "acct-1",
        "status": "connected",
    })
    client = _make_broker_client(
        entry_order_id="never", exit_order_id="never",
        positions_before_close=[],
    )
    client.place_order.side_effect = AssertionError(
        "place_order MUST NOT be called on an exit-only intent with no position"
    )

    with patch(
        "services.public_equity_live_executor._fetch_mark_price",
        new=AsyncMock(return_value=200.0),
    ), patch(
        "services.public_equity_live_executor._public_client",
        return_value=client,
    ):
        out = await maybe_route_live(db, intent={
            "symbol": "AAPL", "direction": "SELL",
            "intent_action": "SELL_TO_CLOSE", "exit_only": True,
            "confidence": 0.80,
        })
    assert out is None
    assert db.equity_live_trades.docs == []
