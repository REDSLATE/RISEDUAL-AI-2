"""Alpaca audit-gap closure tests (2026-05-22).

Pins the three closures of the silent audit gap that hid 995
Alpaca paper orders from Tier 3 / Stage 3 / the outcome bridge:

* ``ml_alpaca_broker.maybe_execute_live`` mirrors every successful
  order into ``paper_trades`` with ``receipt_type="real_fill"``,
  idempotent on ``alpaca_order_id``.
* ``routes/broker.py::_log_order`` mirrors manual UI orders into
  ``paper_trades`` (same idempotency key).
* ``scripts/reconcile_alpaca_orders.py`` exists and exposes the
  dry-run / apply contract operators expect.
"""
from __future__ import annotations

from pathlib import Path


_MLAB = Path("/app/backend/services/ml_alpaca_broker.py").read_text(encoding="utf-8")
_BRK = Path("/app/backend/routes/broker.py").read_text(encoding="utf-8")
_REC = Path("/app/backend/scripts/reconcile_alpaca_orders.py").read_text(encoding="utf-8")


# ── ml_alpaca_broker mirror ────────────────────────────────────────


def test_ml_alpaca_broker_mirrors_orders_into_paper_trades():
    """Every successful Alpaca live order must also write a
    paper_trades row so Tier 3 readiness sees it."""
    # The mirror block lives after the live_orders insert.
    live_pos = _MLAB.find('await db["live_orders"].insert_one(')
    paper_pos = _MLAB.find('await db["paper_trades"].insert_one(')
    assert live_pos > 0
    assert paper_pos > live_pos
    # Carries the doctrine markers
    block = _MLAB[paper_pos:paper_pos + 2000]
    for field in (
        '"alpaca_order_id":',
        '"source_layer": "ml_alpaca_broker"',
        '"receipt_type": "real_fill"',
        '"synthetic": False',
        '"eligible_for_learning": True',
        '"eligible_for_live_unlock": True',
        '"status": "open"',
    ):
        assert field in block, f"mirror missing: {field}"


def test_ml_alpaca_broker_mirror_is_idempotent():
    """A re-call with the same order_id must not duplicate."""
    paper_pos = _MLAB.find('await db["paper_trades"].insert_one(')
    pre_mirror = _MLAB[max(0, paper_pos - 1000):paper_pos]
    assert 'find_one(' in pre_mirror
    assert '"alpaca_order_id": order_id' in pre_mirror


def test_ml_alpaca_broker_mirror_failure_is_non_fatal():
    """A paper_trades write failure must NOT break the broker
    submission (we already have the order ID in Alpaca)."""
    paper_pos = _MLAB.find('await db["paper_trades"].insert_one(')
    after = _MLAB[paper_pos:paper_pos + 2500]
    assert "except Exception" in after
    assert "paper_trades mirror failed" in after


# ── routes/broker.py manual-UI mirror ──────────────────────────────


def test_broker_manual_route_mirrors_into_paper_trades():
    """Manual UI orders submitted via /api/broker/order/{broker_id}
    must also write a paper_trades row."""
    log_order_start = _BRK.find("async def _log_order(")
    next_def = _BRK.find("\n@router", log_order_start)
    block = _BRK[log_order_start:next_def]
    assert "trade_orders.insert_one" in block
    assert 'await db["paper_trades"].insert_one(' in block
    for field in (
        '"alpaca_order_id":',
        '"source_layer": "manual_ui_broker"',
        '"receipt_type": "real_fill"',
        '"synthetic": False',
    ):
        assert field in block, f"manual mirror missing: {field}"


def test_broker_manual_mirror_filled_becomes_closed():
    """If Alpaca already reports status=filled at submission, the
    mirror writes status=closed (otherwise status=open)."""
    log_order_start = _BRK.find("async def _log_order(")
    next_def = _BRK.find("\n@router", log_order_start)
    block = _BRK[log_order_start:next_def]
    assert 'paper_status = "closed" if status == "filled" else "open"' in block


def test_broker_manual_mirror_is_idempotent():
    """Idempotent on broker_order_id."""
    log_order_start = _BRK.find("async def _log_order(")
    next_def = _BRK.find("\n@router", log_order_start)
    block = _BRK[log_order_start:next_def]
    assert 'find_one(' in block
    assert '"alpaca_order_id": broker_oid' in block


# ── Reconciliation script contract ─────────────────────────────────


def test_reconcile_script_has_apply_flag_default_dry_run():
    """Operators expect --apply to be opt-in; without it the script
    is a dry-run that prints stats but writes nothing."""
    assert "--apply" in _REC
    assert 'action="store_true"' in _REC
    assert "if not apply:" in _REC


def test_reconcile_script_is_idempotent():
    """Re-running the reconciler must not double-insert."""
    assert '"alpaca_order_id": oid' in _REC
    # The dedupe check
    assert 'find_one(' in _REC
    assert 'deduped' in _REC


def test_reconcile_script_skips_quarantined_statuses():
    """Canceled / expired / rejected orders must NOT pollute
    paper_trades (Tier 3 ignores them via the alpaca_<status>
    marker)."""
    assert 'alpaca_{s}' in _REC
    assert '"skipped_quarantined"' in _REC


def test_reconcile_script_skips_no_price_rows():
    """Without filled_avg_price we can't compute Stage 3 outcomes,
    so the row is intentionally skipped (with a counter)."""
    assert '"skipped_no_price"' in _REC
    assert "entry <= 0" in _REC


def test_reconcile_script_runnable_as_module():
    """The script must be invocable as ``python -m scripts.reconcile_alpaca_orders``."""
    init = Path("/app/backend/scripts/__init__.py")
    assert init.exists()
    assert 'if __name__ == "__main__":' in _REC
