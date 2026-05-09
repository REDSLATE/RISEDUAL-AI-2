"""ADL-3 contract tests — day_trade_scanner fires shadow receipts.

Pins the fire-and-forget hook added to
``services/day_trade_scanner.py:run_scan`` so EVERY candidate
processed produces an ``alpha_decision_log`` receipt via
``services.ml.receipt_dispatch.schedule_shadow_receipt`` with
``lane="equity"`` and ``source="day_trade"``.

Hard rails pinned by these tests
--------------------------------
* APPROVED candidates (gate_passed=True) produce a receipt.
* Skipped/blocked candidates (gate_passed=False) produce a receipt.
* Pre-signal early returns (empty universe / db missing) produce
  ZERO receipts — the per-candidate loop simply never runs.
* The hook is fire-and-forget — a raising or False-returning
  helper does NOT change ``run_scan``'s return shape.
* ``lane`` is ALWAYS ``"equity"`` per operator brief.
* ``source`` is ALWAYS ``"day_trade"``.
* No broker calls, no order placement — the receipt path is
  observation-only.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from services.day_trade_scanner import ScanCandidate, run_scan


# ── Shared helpers ────────────────────────────────────────────────────────────


def _make_candidate(symbol: str, direction: str = "UP", score: float = 0.80,
                    asset_class: str = "equity") -> ScanCandidate:
    return ScanCandidate(
        symbol=symbol,
        asset_class=asset_class,
        direction=direction,
        score=score,
        confidence_raw=score,
        confidence_calibrated=score,
        prediction_id=f"pred-{symbol}",
        prediction_at="2026-05-09T12:00:00+00:00",
    )


class _FakeDB:
    """Minimal Motor stub. Auto-vivifies any collection access so
    the scanner's reads + writes don't blow up."""

    def __init__(self):
        self._collections: dict = {}

    def __getitem__(self, key):
        if key not in self._collections:
            mock = AsyncMock()
            mock.count_documents = AsyncMock(return_value=0)
            mock.update_one = AsyncMock()
            mock.insert_one = AsyncMock()
            self._collections[key] = mock
        return self._collections[key]

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return self[name]


@pytest.fixture
def schedule_spy(monkeypatch):
    """Spy on the shared ``schedule_shadow_receipt`` helper.

    Patches at the receipt-dispatch source module so the deferred
    import inside ``run_scan`` resolves to our spy.
    """
    import services.ml.receipt_dispatch as receipt_dispatch
    spy = MagicMock(return_value=True)
    monkeypatch.setattr(receipt_dispatch, "schedule_shadow_receipt", spy)
    return spy


@pytest.fixture
def scan_universe_stub(monkeypatch):
    """Replace scan_universe with a programmable stub so we can
    inject a deterministic candidate set without touching Mongo."""
    candidates: list[ScanCandidate] = []

    async def _stub(_db, _asset_class, *_, **__):
        return list(candidates)

    monkeypatch.setattr(
        "services.day_trade_scanner.scan_universe", _stub,
    )
    return candidates


@pytest.fixture
def apply_gates_stub(monkeypatch):
    """Replace apply_gates with a programmable stub so we can
    drive APPROVED vs blocked deterministically per candidate."""
    verdicts: dict[str, tuple[bool, str | None]] = {}

    async def _stub(_db, candidate):
        passed, blocker = verdicts.get(candidate.symbol, (True, None))
        return passed, blocker, {"stub": True}

    monkeypatch.setattr(
        "services.day_trade_scanner.apply_gates", _stub,
    )
    return verdicts


# ── APPROVED path ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_approved_candidate_schedules_receipt(
    schedule_spy, scan_universe_stub, apply_gates_stub,
):
    db = _FakeDB()
    scan_universe_stub.append(_make_candidate("AAPL", score=0.80))
    apply_gates_stub["AAPL"] = (True, None)

    result = await run_scan(db, "equity")

    assert schedule_spy.call_count == 1
    assert result.chosen is not None
    assert result.chosen.symbol == "AAPL"


@pytest.mark.asyncio
async def test_approved_path_uses_correct_lane_and_source(
    schedule_spy, scan_universe_stub, apply_gates_stub,
):
    db = _FakeDB()
    scan_universe_stub.append(_make_candidate("AAPL", score=0.80))
    apply_gates_stub["AAPL"] = (True, None)

    await run_scan(db, "equity")

    kwargs = schedule_spy.call_args.kwargs
    assert kwargs.get("lane") == "equity", (
        "lane must be 'equity' per ADL-3 operator brief"
    )
    assert kwargs.get("source") == "day_trade", (
        "source identifies the day-trade scanner entry point"
    )


@pytest.mark.asyncio
async def test_approved_signal_payload_contains_required_keys(
    schedule_spy, scan_universe_stub, apply_gates_stub,
):
    db = _FakeDB()
    scan_universe_stub.append(_make_candidate("AAPL", score=0.80))
    apply_gates_stub["AAPL"] = (True, None)

    await run_scan(db, "equity")

    sig = schedule_spy.call_args.kwargs.get("signal")
    assert isinstance(sig, dict)
    assert sig["symbol"] == "AAPL"
    assert sig["gate_passed"] is True
    assert sig["gate_blocker"] is None
    assert sig["source_layer"] == "day_trade_scanner"


# ── NO_TRADE / blocked path ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_blocked_candidate_still_schedules_receipt(
    schedule_spy, scan_universe_stub, apply_gates_stub,
):
    db = _FakeDB()
    scan_universe_stub.append(_make_candidate("AAPL", score=0.40))
    apply_gates_stub["AAPL"] = (False, "below_min_score")

    result = await run_scan(db, "equity")

    assert schedule_spy.call_count == 1
    assert result.chosen is None
    sig = schedule_spy.call_args.kwargs.get("signal")
    assert sig["gate_passed"] is False
    assert sig["gate_blocker"] == "below_min_score"


@pytest.mark.asyncio
async def test_blocked_path_lane_and_source_match_approved(
    schedule_spy, scan_universe_stub, apply_gates_stub,
):
    db = _FakeDB()
    scan_universe_stub.append(_make_candidate("AAPL"))
    apply_gates_stub["AAPL"] = (False, "non_directional_prediction")

    await run_scan(db, "equity")

    kwargs = schedule_spy.call_args.kwargs
    assert kwargs.get("lane") == "equity"
    assert kwargs.get("source") == "day_trade"


# ── Multiple candidates: every candidate produces a receipt ───────────────────


@pytest.mark.asyncio
async def test_every_candidate_produces_one_receipt(
    schedule_spy, scan_universe_stub, apply_gates_stub,
):
    db = _FakeDB()
    scan_universe_stub.extend([
        _make_candidate("AAPL", score=0.80),
        _make_candidate("MSFT", score=0.60),
        _make_candidate("TSLA", score=0.30),
    ])
    apply_gates_stub["AAPL"] = (True, None)
    apply_gates_stub["MSFT"] = (False, "already_holding")
    apply_gates_stub["TSLA"] = (False, "below_min_score")

    await run_scan(db, "equity")

    # 3 candidates → 3 receipts (1 APPROVED + 2 blocked).
    assert schedule_spy.call_count == 3
    syms_logged = {
        call.kwargs["signal"]["symbol"]
        for call in schedule_spy.call_args_list
    }
    assert syms_logged == {"AAPL", "MSFT", "TSLA"}


# ── Pre-signal early returns: NO receipts ─────────────────────────────────────


@pytest.mark.asyncio
async def test_empty_universe_logs_no_receipts(
    schedule_spy, scan_universe_stub, apply_gates_stub,
):
    db = _FakeDB()
    # scan_universe returns [] — no candidates to process.

    result = await run_scan(db, "equity")

    schedule_spy.assert_not_called()
    assert result.total_scanned == 0
    assert result.chosen is None


@pytest.mark.asyncio
async def test_db_none_does_not_call_helper(schedule_spy, monkeypatch):
    """When db is None, scan_universe returns [] (its own guard).
    The per-candidate loop never runs — no receipts."""
    async def _empty(_db, _ac, *_, **__):
        return []

    monkeypatch.setattr(
        "services.day_trade_scanner.scan_universe", _empty,
    )

    result = await run_scan(None, "equity")

    schedule_spy.assert_not_called()
    assert result.total_scanned == 0


# ── Failure isolation ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_helper_raise_does_not_change_run_scan_result(
    monkeypatch, scan_universe_stub, apply_gates_stub,
):
    """A raising helper must NOT alter ``run_scan``'s return value
    or break the scan loop. Belt+braces try/except at the call
    site catches it and proceeds."""
    db = _FakeDB()
    scan_universe_stub.append(_make_candidate("AAPL", score=0.80))
    apply_gates_stub["AAPL"] = (True, None)

    def _raising(*_args, **_kwargs):
        raise RuntimeError("synthetic ADL helper failure")

    import services.ml.receipt_dispatch as receipt_dispatch
    monkeypatch.setattr(receipt_dispatch, "schedule_shadow_receipt", _raising)

    # Must NOT raise.
    result = await run_scan(db, "equity")

    # Scan still produced a chosen candidate — execution path
    # entirely unaffected by the helper failure.
    assert result.chosen is not None
    assert result.chosen.symbol == "AAPL"


@pytest.mark.asyncio
async def test_helper_returning_false_does_not_branch_caller(
    schedule_spy, scan_universe_stub, apply_gates_stub,
):
    """``schedule_shadow_receipt`` returns False on no-loop / no-Mongo
    cases. ``run_scan`` MUST treat False purely as diagnostic."""
    schedule_spy.return_value = False
    db = _FakeDB()
    scan_universe_stub.append(_make_candidate("AAPL", score=0.80))
    apply_gates_stub["AAPL"] = (True, None)

    result = await run_scan(db, "equity")

    schedule_spy.assert_called_once()
    assert result.chosen is not None
    assert result.chosen.symbol == "AAPL"


# ── Static authority firewall ─────────────────────────────────────────────────


def test_day_trade_scanner_does_not_call_run_shadow_pipeline_directly():
    """The scanner must use the shared dispatcher, NOT call
    ``run_shadow_pipeline`` itself — that would re-introduce the
    create_task boilerplate and risk diverging from the helper's
    failure-isolation contract."""
    from pathlib import Path

    src = Path(
        "/app/backend/services/day_trade_scanner.py"
    ).read_text(encoding="utf-8")

    assert "run_shadow_pipeline(" not in src, (
        "day_trade_scanner must use schedule_shadow_receipt(...) "
        "and never call run_shadow_pipeline directly."
    )
    assert "schedule_shadow_receipt" in src, (
        "day_trade_scanner must wire to schedule_shadow_receipt."
    )


def test_day_trade_scanner_does_not_import_broker_or_executor():
    """The receipt path must NOT add broker or executor imports —
    receipts are observation-only, broker calls happen elsewhere."""
    from pathlib import Path

    src = Path(
        "/app/backend/services/day_trade_scanner.py"
    ).read_text(encoding="utf-8")

    forbidden = [
        "from services.broker_service",
        "from services.trading_bot_service",
        "from services.crypto_paper_trader",
        "from services.paper_trading_service",
        "from routes.broker",
        "from services.ml.executors",
        "from services.ml.broker_wire",
        ".place_order(",
        "broker.execute(",
    ]
    found = [tok for tok in forbidden if tok in src]
    assert not found, (
        "day_trade_scanner introduced forbidden broker/executor "
        f"reference(s): {found}"
    )
