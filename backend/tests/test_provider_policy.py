"""Tests for the decision-type-based provider policy.

Covers the three moving pieces:

* ``PROVIDER_POLICY`` table shape / ``get_provider_chain`` fallback
* ``compute_disagreement_bps`` math
* ``fetch_execution_quote`` freshness / drift / broker-first gates
"""

from __future__ import annotations

import time

import pytest

from services import provider_policy


# ─────────────────────────────────────────────
#  Policy table
# ─────────────────────────────────────────────
def test_policy_table_has_required_decision_types():
    """The seven decision types spec'd by the operator must be present.

    Regression guard — if someone renames a key we want the test
    to fail loudly instead of the caller silently falling back to
    the execution-quote chain.
    """
    required = {
        "execution_quote",
        "account_state",
        "positions",
        "open_orders",
        "intraday_regime",
        "daily_history",
        "news",
    }
    assert required.issubset(provider_policy.PROVIDER_POLICY.keys())


def test_execution_quote_puts_broker_first():
    """Live execution truth = broker first, always."""
    chain = provider_policy.get_provider_chain("execution_quote")
    assert chain[0] == "broker"
    assert "finnhub" in chain
    assert "polygon" in chain


def test_account_state_is_broker_only():
    """Cash / buying power / positions must never fall back to a
    vendor — vendors don't know our account."""
    for dt in ("account_state", "positions", "open_orders"):
        assert provider_policy.get_provider_chain(dt) == ["broker"]


def test_daily_history_is_vendor_first():
    """Research / backtest should use vendor bars — broker daily
    bars are often adjusted differently and rate-limited."""
    chain = provider_policy.get_provider_chain("daily_history")
    assert chain[0] != "broker"
    assert "polygon" in chain


def test_unknown_decision_type_defaults_to_execution_quote_chain():
    """A typo shouldn't silently drop the broker off the front."""
    default = provider_policy.get_provider_chain("something_that_does_not_exist")
    assert default == provider_policy.get_provider_chain("execution_quote")


def test_get_provider_chain_returns_copy_not_reference():
    """Callers must not be able to mutate the policy table."""
    chain = provider_policy.get_provider_chain("execution_quote")
    chain.append("EVIL")
    assert "EVIL" not in provider_policy.PROVIDER_POLICY["execution_quote"]


# ─────────────────────────────────────────────
#  Drift math
# ─────────────────────────────────────────────
def test_compute_disagreement_bps_basic():
    """A 1% drift is 100 bps by definition."""
    assert provider_policy.compute_disagreement_bps(100.0, 101.0) == pytest.approx(100.0)


def test_compute_disagreement_bps_symmetric_abs():
    """The function returns absolute drift; up and down 1% agree."""
    up = provider_policy.compute_disagreement_bps(100.0, 101.0)
    down = provider_policy.compute_disagreement_bps(100.0, 99.0)
    assert up == pytest.approx(down)


def test_compute_disagreement_bps_zero_reference_is_zero():
    """A zero reference is undefined; we return 0.0 so callers
    don't accidentally compare NaN against a threshold."""
    assert provider_policy.compute_disagreement_bps(0.0, 100.0) == 0.0
    assert provider_policy.compute_disagreement_bps(-1.0, 100.0) == 0.0


def test_compute_disagreement_bps_at_threshold_boundary():
    """A 0.5% drift = exactly the default 50 bps ceiling. We treat
    the ceiling as inclusive of "still OK" — the trade path only
    blocks when drift *exceeds* it."""
    drift = provider_policy.compute_disagreement_bps(100.0, 100.5)
    assert drift == pytest.approx(50.0)
    assert drift <= provider_policy.EXECUTION_MAX_DRIFT_BPS


# ─────────────────────────────────────────────
#  Execution-quote gate
# ─────────────────────────────────────────────
def _install_stub_quotes(monkeypatch, *, broker, vendor):
    """Point the policy module at fake broker / vendor fetchers.

    Each fake returns whatever dict the test asks for (or ``None``).
    Kept as a helper so every test reads as a data table.
    """
    async def _broker(_symbol):
        return broker

    async def _vendor(_symbol):
        return vendor

    import services.market_data_pool as pool_mod
    monkeypatch.setattr(pool_mod, "fetch_broker_quote", _broker)
    monkeypatch.setattr(pool_mod, "fetch_vendor_quote", _vendor)


def _run_gate(symbol: str):
    """Drive :func:`fetch_execution_quote` from a sync test.

    We deliberately DON'T use ``asyncio.run`` here — it closes the
    default event loop on exit, which breaks any later test that
    still relies on the legacy ``asyncio.get_event_loop()`` pattern.
    A private loop scoped to this call is set up and torn down
    cleanly instead.
    """
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(provider_policy.fetch_execution_quote(symbol))
    finally:
        loop.close()


def test_fresh_broker_price_agrees_with_vendor_execution_allowed(monkeypatch):
    """The happy path: fresh broker, vendor confirms, no drift.

    ``execution_allowed`` must be True, ``source == "broker"``,
    and the drift number must be recorded for the audit trail
    even when it's under the ceiling.
    """
    now = time.time()
    _install_stub_quotes(
        monkeypatch,
        broker={"price": 150.00, "fetched_at": now, "source": "public"},
        vendor={"price": 150.05, "fetched_at": now, "source": "finnhub", "provider_name": "finnhub-backup"},
    )
    xq = _run_gate("AAPL")
    assert xq.execution_allowed is True
    assert xq.source == "broker"
    assert xq.price == 150.00
    assert xq.broker_price == 150.00
    assert xq.vendor_price == 150.05
    assert xq.disagreement_bps is not None
    assert xq.disagreement_bps < provider_policy.EXECUTION_MAX_DRIFT_BPS
    assert xq.data_conflict is False
    assert xq.reason == "broker_confirmed"


def test_stale_broker_quote_blocks_execution(monkeypatch):
    """Broker price older than the freshness ceiling must not be
    trusted for auto-execution, even if a vendor confirms it."""
    old = time.time() - (provider_policy.EXECUTION_FRESHNESS_SECS + 10)
    _install_stub_quotes(
        monkeypatch,
        broker={"price": 150.00, "fetched_at": old, "source": "public"},
        vendor={"price": 150.00, "fetched_at": time.time(), "source": "finnhub"},
    )
    xq = _run_gate("AAPL")
    assert xq.execution_allowed is False
    assert xq.reason is not None and "stale" in xq.reason
    assert xq.source == "broker"
    assert xq.age_seconds is not None
    assert xq.age_seconds > provider_policy.EXECUTION_FRESHNESS_SECS


def test_broker_vendor_disagreement_over_50bps_blocks_execution(monkeypatch):
    """The user-spec'd disagreement rule: broker $150 vs vendor
    $151.50 = 100 bps drift > 50 bps default ceiling → block.

    We must still report the broker price (so the operator can see
    what the broker said), we must NOT allow auto-execute, and the
    ``data_conflict`` flag must be True.
    """
    now = time.time()
    _install_stub_quotes(
        monkeypatch,
        broker={"price": 150.00, "fetched_at": now, "source": "public"},
        vendor={"price": 151.50, "fetched_at": now, "source": "finnhub", "provider_name": "finnhub-backup"},
    )
    xq = _run_gate("AAPL")
    assert xq.execution_allowed is False
    assert xq.data_conflict is True
    assert xq.disagreement_bps is not None
    assert xq.disagreement_bps > provider_policy.EXECUTION_MAX_DRIFT_BPS
    assert xq.reason is not None and "data_conflict" in xq.reason
    # Broker price still surfaced for the audit trail.
    assert xq.broker_price == 150.00
    assert xq.vendor_price == 151.50


def test_broker_missing_falls_through_to_vendor_but_blocks_execution(monkeypatch):
    """When the broker has no quote (symbol not in coverage / broker
    offline) we return the vendor price for context but block
    auto-execute. The trade path must re-quote the broker itself.
    """
    _install_stub_quotes(
        monkeypatch,
        broker=None,
        vendor={"price": 150.00, "fetched_at": time.time(), "source": "finnhub", "provider_name": "finnhub-backup"},
    )
    xq = _run_gate("XYZ")
    assert xq.execution_allowed is False
    assert xq.reason == "no_broker_price"
    assert xq.price == 150.00
    assert xq.source and xq.source.startswith("vendor:")


def test_no_price_from_anyone_returns_disallowed(monkeypatch):
    """Total blackout — both feeds silent. The gate must fail
    closed with ``no_price`` and no bogus fields."""
    _install_stub_quotes(monkeypatch, broker=None, vendor=None)
    xq = _run_gate("XYZ")
    assert xq.execution_allowed is False
    assert xq.reason == "no_price"
    assert xq.price is None
    assert xq.broker_price is None
    assert xq.vendor_price is None


def test_broker_price_without_fetched_at_is_treated_as_stale(monkeypatch):
    """A broker feed that forgets to stamp a timestamp is not
    trustworthy for the freshness gate. We keep the response but
    refuse auto-execute so the caller notices."""
    _install_stub_quotes(
        monkeypatch,
        broker={"price": 150.00, "source": "public"},  # no fetched_at
        vendor={"price": 150.00, "fetched_at": time.time(), "source": "finnhub"},
    )
    xq = _run_gate("AAPL")
    assert xq.execution_allowed is False
    assert xq.reason is not None and "stale" in xq.reason


# ─────────────────────────────────────────────
#  Stale-vendor witness — the AV free-tier 15-min-delay case
# ─────────────────────────────────────────────
def test_stale_vendor_cannot_veto_fresh_broker(monkeypatch):
    """The exact ADBE-live case from Monday's open: broker $285.80
    live, vendor $292.79 from Alpha Vantage (silently 15-min delayed).
    244 bps drift is well over the 50 bps ceiling BUT the vendor
    is in the ``DELAYED_QUOTE_PROVIDERS`` set so it cannot veto.

    Must:
      * still record the drift number for the audit trail
      * NOT set ``data_conflict`` (the drift isn't real, the
        witness silently returns delayed prices)
      * allow execution against the fresh broker price
    """
    now = time.time()
    _install_stub_quotes(
        monkeypatch,
        broker={"price": 285.80, "fetched_at": now, "source": "public"},
        # AV stamps the RECEIVE time so ``fetched_at`` is fresh even
        # though the underlying price is 15 min old — this is the
        # exact production shape we saw Monday's open.
        vendor={"price": 292.79,
                "fetched_at": now,
                "source": "alphavantage",
                "provider_name": "alphavantage-backup"},
    )
    xq = _run_gate("ADBE")
    # drift is still computed and surfaced for observability
    assert xq.disagreement_bps is not None
    assert xq.disagreement_bps > provider_policy.EXECUTION_MAX_DRIFT_BPS
    # but data_conflict is NOT set — AV is known-delayed
    assert xq.data_conflict is False
    # and the trade is ALLOWED against the fresh broker price
    assert xq.execution_allowed is True
    assert xq.source == "broker"
    assert xq.price == 285.80


def test_alphavantage_name_variants_all_bypass_drift(monkeypatch):
    """Whatever we name the AV entry in the pool
    (``alphavantage``, ``alphavantage-backup``, ``ALPHAVANTAGE``),
    the drift-witness bypass must catch it. Prefix + lowercase
    matching guards against a rename accidentally re-arming veto.
    """
    now = time.time()
    for source_name in ("alphavantage", "alphavantage-backup",
                        "ALPHAVANTAGE-primary"):
        _install_stub_quotes(
            monkeypatch,
            broker={"price": 100.0, "fetched_at": now, "source": "public"},
            vendor={"price": 110.0,           # 1000 bps drift
                    "fetched_at": now,
                    "provider_name": source_name},
        )
        xq = _run_gate("AAPL")
        assert xq.data_conflict is False, (
            f"AV variant {source_name!r} should not veto — it's delayed"
        )


def test_fresh_vendor_still_vetoes_when_drift_exceeds():
    """The original protection is intact: when the vendor IS
    fresh and drifts > 50 bps from the broker, ``data_conflict``
    still trips and blocks execution. That's the real-disagreement
    case we still want to catch."""
    now = time.time()
    with pytest.MonkeyPatch.context() as monkeypatch:
        _install_stub_quotes(
            monkeypatch,
            broker={"price": 150.00, "fetched_at": now, "source": "public"},
            vendor={"price": 151.50,
                    "fetched_at": now,   # fresh witness
                    "source": "finnhub",
                    "provider_name": "finnhub-backup"},
        )
        xq = _run_gate("AAPL")
    assert xq.data_conflict is True
    assert xq.execution_allowed is False


def test_vendor_age_boundary_at_configured_ceiling():
    """A vendor exactly at ``EXECUTION_VENDOR_MAX_AGE_SECS`` old
    is still considered fresh (``<=`` in the check). One second
    older and it's no longer a credible witness."""
    now = time.time()
    max_age = provider_policy.EXECUTION_VENDOR_MAX_AGE_SECS

    with pytest.MonkeyPatch.context() as monkeypatch:
        _install_stub_quotes(
            monkeypatch,
            broker={"price": 100.0, "fetched_at": now, "source": "public"},
            vendor={"price": 110.0,   # 1000 bps drift
                    # A hair inside the ceiling so the tiny time
                    # elapsed inside the gate doesn't push us over.
                    "fetched_at": now - max_age + 1,
                    "source": "finnhub"},
        )
        xq_at = _run_gate("A")
    assert xq_at.data_conflict is True, (
        "vendor inside the age ceiling must still be trusted"
    )

    with pytest.MonkeyPatch.context() as monkeypatch:
        _install_stub_quotes(
            monkeypatch,
            broker={"price": 100.0, "fetched_at": now, "source": "public"},
            vendor={"price": 110.0,
                    "fetched_at": now - max_age - 1,   # 1s over
                    "source": "finnhub"},
        )
        xq_over = _run_gate("B")
    assert xq_over.data_conflict is False


def test_vendor_missing_fetched_at_does_not_veto_fresh_broker():
    """A vendor payload without ``fetched_at`` has unknown age —
    we must treat it conservatively (unknown = not credible as a
    drift witness) so it can't accidentally veto a live broker
    quote. Same principle as the AV-15min case."""
    with pytest.MonkeyPatch.context() as monkeypatch:
        _install_stub_quotes(
            monkeypatch,
            broker={"price": 100.0, "fetched_at": time.time(), "source": "public"},
            vendor={"price": 110.0, "source": "unknown"},   # no fetched_at
        )
        xq = _run_gate("A")
    assert xq.data_conflict is False
    assert xq.execution_allowed is True
