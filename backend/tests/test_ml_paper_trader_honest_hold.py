"""Honest-hold-on-Kelly-zero — 2026-05-22 doctrine pin.

Pins the change to ``services/ml_paper_trader.py::maybe_paper_trade``
so that when ``half_kelly_position`` returns 0, the function does NOT
silently ``return None``. Instead, it emits an honest-hold receipt to
MC with the contract:

    raw_action:                       BUY | SELL
    market_decision:                  HOLD
    execution_decision:               OBSERVE_ONLY
    would_have_traded_without_gates:  False
    hold_reason:                      kelly_zero_size

Why this matters
----------------
Without this telemetry, MC cannot distinguish "Alpha was bearish
(genuine HOLD)" from "Alpha was bullish but Kelly self-throttled to
$0 on weak conviction (Kelly-zero HOLD)". Patent J + Stage 3
calibration depend on knowing the difference.

Hard rails
----------
* The honest-hold emit fires INSIDE the ``position_usd <= 0`` branch
  AND BEFORE the ``return None``.
* It is wrapped in ``try/except`` so an MC outage never breaks Alpha's
  main loop.
* Local paper-trade behaviour is unchanged — still no ``paper_trades``
  row written on Kelly-zero.
* The honest-hold receipt carries the five-field doctrine envelope.
"""
from __future__ import annotations

from pathlib import Path

import pytest


_SRC_PATH = Path("/app/backend/services/ml_paper_trader.py")


@pytest.fixture(scope="module")
def src() -> str:
    return _SRC_PATH.read_text(encoding="utf-8")


# ── Static authority firewall ──────────────────────────────────────


def test_kelly_zero_branch_emits_honest_hold(src: str):
    """The Kelly-zero branch must contain an
    ``emit_intent_from_consensus`` call."""
    branch_start = src.find("if position_usd <= 0.0:")
    assert branch_start > 0, "Kelly-zero branch missing"
    branch_end = src.find("return None", branch_start)
    assert branch_end > branch_start
    branch = src[branch_start:branch_end]
    assert "emit_intent_from_consensus" in branch, (
        "Kelly-zero branch must emit an honest-hold receipt, not silently return None"
    )


def test_honest_hold_carries_doctrine_envelope(src: str):
    """The receipt payload must declare the five-field honesty
    envelope so MC / Patent J / Stage 3 ledger can recognise it."""
    branch_start = src.find("if position_usd <= 0.0:")
    branch_end = src.find("return None", branch_start)
    branch = src[branch_start:branch_end]
    for field in (
        '"market_decision": "HOLD"',
        '"execution_decision": "OBSERVE_ONLY"',
        '"would_have_traded_without_gates": False',
        '"hold_reason": "kelly_zero_size"',
        '"raw_action"',
        '"display_action"',
    ):
        assert field in branch, f"honest-hold receipt missing field: {field}"


def test_honest_hold_emit_is_wrapped_in_try_except(src: str):
    """An MC outage must never break Alpha's main loop."""
    branch_start = src.find("if position_usd <= 0.0:")
    branch_end = src.find("return None", branch_start)
    branch = src[branch_start:branch_end]
    assert "try:" in branch
    assert "except Exception" in branch
    assert "honest-hold emit failed" in branch.lower()


def test_kelly_zero_branch_defines_direction_val_locally(src: str):
    """2026-02-23 regression pin: ``direction_val`` was referenced
    in the Kelly-zero branch BEFORE it was assigned (the original
    assignment lived at the post-Kelly success path). Every
    Kelly-zero tick was raising ``UnboundLocalError``, silently
    swallowed by the broad ``except`` block — meaning every
    honest-hold receipt AND every observation_fill row was
    DROPPED in prod. Pin the local definition so a future
    refactor can't accidentally remove it again."""
    branch_start = src.find("if position_usd <= 0.0:")
    branch_end = src.find("return None", branch_start)
    branch = src[branch_start:branch_end]
    # The branch must assign direction_val locally, BEFORE any use.
    first_assign = branch.find("direction_val = str(signal.direction.value)")
    assert first_assign > 0, (
        "Kelly-zero branch MUST assign ``direction_val`` locally to "
        "guard against UnboundLocalError (see 2026-02-23 RCA)."
    )
    # All later usages must occur AFTER the assignment.
    for marker in ('"direction": direction_val',
                   "direction_val.upper()"):
        pos = branch.find(marker)
        if pos > 0:
            assert pos > first_assign, (
                f"Kelly-zero branch reference {marker!r} appears "
                f"BEFORE direction_val is assigned — would re-introduce "
                f"the silent UnboundLocalError"
            )


def test_kelly_zero_branch_uses_correct_up_down_check(src: str):
    """2026-02-23 regression pin: the original BUY/SELL logic used
    ``direction_val == "long"`` — but ``signal.direction.value``
    returns ``"up"``/``"down"``, NOT ``"long"``/``"short"``. So
    every honest-hold emit would have stamped ``SELL`` regardless
    of true brain direction (had the UnboundLocalError NOT fired
    first to mask it). Pin the corrected ``"up"`` check."""
    branch_start = src.find("if position_usd <= 0.0:")
    branch_end = src.find("return None", branch_start)
    branch = src[branch_start:branch_end]
    # The wrong literal must NOT be present.
    assert 'direction_val == "long"' not in branch, (
        "Kelly-zero branch reverted to the broken ``\"long\"`` check "
        "— signal.direction.value returns ``\"up\"``/``\"down\"``."
    )
    # The corrected check (or an equivalent ``_is_long`` derived
    # variable) must be present.
    assert (
        'direction_val == "up"' in branch
        or '_is_long' in branch
    ), "Kelly-zero branch missing the up/down direction discriminator"


def test_kelly_zero_branch_does_not_reference_unassigned_globals(src: str):
    """2026-02-23 regression pin: ``price_at_signal`` was NEVER
    assigned anywhere in ``maybe_paper_trade`` but the
    observation_fill ``entry_price`` field referenced it. NameError
    on every Kelly-zero tick. Pin the corrected pattern (snapshot
    fallback)."""
    branch_start = src.find("if position_usd <= 0.0:")
    branch_end = src.find("return None", branch_start)
    branch = src[branch_start:branch_end]
    # ``price_at_signal`` must NOT be referenced as a bare name.
    # Wrap-style usages (``getattr(snapshot, ...``) are fine; the
    # raw bareword that the original bug carried is what we ban.
    assert "price_at_signal" not in branch, (
        "Kelly-zero branch still references the never-assigned "
        "``price_at_signal`` — use ``snapshot.close_price`` instead."
    )
    # The corrected entry_price source must be present.
    assert 'getattr(snapshot, "close_price"' in branch, (
        "Kelly-zero observation_fill must use snapshot.close_price "
        "for entry_price (the 2026-02-23 fix)."
    )


def test_kelly_zero_branch_defends_sovereign_decision_id_unbound(src: str):
    """2026-02-23 regression pin: ``sovereign_decision_id`` is only
    assigned inside the (try-guarded) sovereign-shadow branch. If
    that branch is skipped or fails, the observation_fill write
    would raise UnboundLocalError. Pin the ``locals().get(...)``
    defensive read."""
    branch_start = src.find("if position_usd <= 0.0:")
    branch_end = src.find("return None", branch_start)
    branch = src[branch_start:branch_end]
    # The defensive read must be present.
    assert 'locals().get("sovereign_decision_id")' in branch, (
        "Kelly-zero branch must read sovereign_decision_id via "
        "``locals().get(...)`` so an upstream shadow skip doesn't "
        "raise UnboundLocalError."
    )


def test_kelly_zero_branch_still_returns_none(src: str):
    """The honest-hold emit MUST precede ``return None``.

    Updated 2026-05-22 for the observation_fill rung: the branch
    now DOES write a paper_trades row, but it is an
    ``observation_fill`` (status=observation_open, synthetic=True,
    shares=0). The pin protects against accidentally writing a
    REAL fill (status="open" with shares > 0)."""
    branch_start = src.find("if position_usd <= 0.0:")
    branch_end = src.find("return None", branch_start)
    # The return None must still be there.
    assert branch_end > branch_start
    branch = src[branch_start:branch_end]
    # And no REAL paper-trade insert (status="open" with shares > 0).
    # The observation row is allowed; check the markers that
    # distinguish observations from real fills.
    assert '"status": "observation_open"' in branch
    assert '"synthetic": True' in branch
    assert '"shares": 0.0' in branch
    assert '"position_usd": 0.0' in branch


# ── Behavioural test — patch MC, drive the branch, assert call ────


@pytest.mark.asyncio
async def test_kelly_zero_calls_emit_intent_from_consensus(monkeypatch):
    """Drive ``maybe_paper_trade`` down the Kelly-zero branch and
    confirm ``emit_intent_from_consensus`` is invoked exactly once
    with ``hold_reason='kelly_zero_size'``.

    Heavy stubs — we're testing the wiring, not the Sovereign /
    failure-penalty / sizing stack.
    """
    from unittest.mock import AsyncMock, MagicMock, patch

    # Force kelly to return 0.
    with patch(
        "services.ml_paper_trader.half_kelly_position",
        return_value=0.0,
    ), patch(
        "services.ml_paper_trader._current_portfolio_value",
        new=AsyncMock(return_value=100_000.0),
    ), patch(
        "sovereign.intent_bridge.emit_intent_from_consensus",
        new=AsyncMock(return_value={"ok": True}),
    ) as mock_emit, patch(
        "sovereign.mc_client.MCClient",
        return_value=MagicMock(),
    ):
        # Disable the council penalty / sovereign / failure-penalty
        # branches so we reach kelly with a simple direction.
        from services import ml_paper_trader as mod

        # Stub `get_failure_penalty` to no-op
        monkeypatch.setattr(
            "services.ml_paper_trader.get_failure_penalty",
            AsyncMock(return_value=None),
            raising=False,
        )

        # Build the absolute minimum Signal-like object the function
        # needs to reach the Kelly branch.
        signal = MagicMock()
        signal.direction = MagicMock(value="up")
        # 0.65 is the realistic Kelly-zero scenario — low conviction
        # but still above the static ``_MIN_PAPER_CONFIDENCE=0.55``
        # floor. The dynamic confidence gate
        # (``get_dynamic_confidence_threshold``) is stubbed below so
        # the in-memory drawdown / loss-streak escalators don't
        # raise the bar above 0.65 during the test run.
        signal.confidence = 0.65
        signal.prediction_id = "pred-test-001"
        signal.feature_importance = {}

        # Stub the dynamic confidence gate to a permissive threshold
        # so signal.confidence=0.65 reaches the Kelly check.
        from services import confidence_gate as _cg
        _ConfThresh = type("_ConfThresh", (), {})
        _stub = _ConfThresh()
        _stub.threshold = 0.50
        _stub.delta = 0.0
        _stub.reasons = []
        monkeypatch.setattr(
            "services.confidence_gate.get_dynamic_confidence_threshold",
            AsyncMock(return_value=_stub),
            raising=False,
        )

        snapshot = MagicMock()
        for attr in ("rsi", "momentum_5b", "atr_pct", "volume_zscore",
                     "dollar_volume", "dollar_volume_baseline"):
            setattr(snapshot, attr, None)

        # Fake db with the absolute minimum collections accessed
        # before the Kelly check.
        class _Coll:
            async def find_one(self, *a, **kw):
                return None

            async def insert_one(self, *a, **kw):
                return MagicMock(inserted_id="x")

            def find(self, *a, **kw):
                class _Cur:
                    def sort(self, *a, **kw):
                        return self

                    def limit(self, *a, **kw):
                        return self

                    async def to_list(self, *a, **kw):
                        return []

                    def __aiter__(self):
                        return self

                    async def __anext__(self):
                        raise StopAsyncIteration
                return _Cur()

            async def count_documents(self, *a, **kw):
                return 0

            async def update_one(self, *a, **kw):
                return MagicMock(modified_count=0)

        class _DB:
            def __getitem__(self, _name):
                return _Coll()

            def __getattr__(self, _name):
                return _Coll()

        try:
            result = await mod.maybe_paper_trade(
                # Production signature (post-refactor):
                #   maybe_paper_trade(ticker, signal, snapshot, regime, db, http_client=None)
                # The earlier test fixture passed args in the old order
                # (db-first) which made the function read the str
                # ticker AS the signal and threw
                # ``'str' object has no attribute 'direction'`` before
                # even reaching the Kelly branch — triggering the
                # defensive ``pytest.skip``. Pinning the call order
                # against the actual signature keeps this behavioural
                # test honest (the 2026-02-23 mock-drift fix).
                #
                # ``regime`` must be one of ``_TRADEABLE_REGIMES`` —
                # ``"trend_up"`` (legacy) is NOT in the set; the
                # canonical token is ``"trending_up"``. Using the
                # wrong token short-circuits at line 398 before Kelly
                # runs.
                "AAPL", signal, snapshot, "trending_up", _DB(),
            )
        except Exception as exc:  # noqa: BLE001
            # If maybe_paper_trade evolves AGAIN and we no longer
            # reach Kelly, fail loudly rather than skip silently —
            # the static authority tests above still pin the
            # doctrine fields, but a hard fail here surfaces the
            # mock drift immediately instead of letting it rot
            # under a green ``skipped`` badge.
            pytest.fail(
                f"maybe_paper_trade aborted before kelly (mock drift): {exc!r}"
            )

        # On Kelly-zero, we must return None
        assert result is None
        # And the honest-hold emit must have fired exactly once
        # with the expected hold_reason.
        assert mock_emit.call_count == 1, (
            f"expected exactly one honest-hold emit, got {mock_emit.call_count}"
        )
        call_kwargs = mock_emit.call_args
        # Second positional arg is the receipt dict.
        receipt = call_kwargs.args[1] if len(call_kwargs.args) > 1 else None
        assert receipt is not None, "honest-hold emit got no receipt arg"
        assert receipt["hold_reason"] == "kelly_zero_size"
        assert receipt["market_decision"] == "HOLD"
        assert receipt["execution_decision"] == "OBSERVE_ONLY"
        assert receipt["would_have_traded_without_gates"] is False
        assert receipt["raw_action"] == "BUY"  # direction.value == "up"
