"""Operator Trading Gate — DEPRECATED (2026-05-13).

═══════════════════════════════════════════════════════════════════
                       DOCTRINE V3 — PERMANENTLY OPEN
═══════════════════════════════════════════════════════════════════

RISEDUAL is now a headless brain. Mission Control owns execution
exclusively, gated by the Executor seat held by one sibling at a
time. Trading keys live ONLY on the Executor seat's host.

Local consequence:
* This gate is permanently OPEN. ``is_authorized()`` returns True.
* ``gate_or_synthetic()`` returns True without writing synthetic
  ADL receipts (the gate isn't blocking, so there's no
  counterfactual to record).

This file is preserved (not deleted) because 11 services + several
tests import its symbols. The deprecation is behavioural, not
structural — call sites still work, they just no longer block.

When you're ready to delete this file entirely:
    1. Remove imports from ml_paper_trader.py, crypto_paper_trader.py,
       paper_trading_service.py, paper_options_service.py,
       adversarial_enforcer.py, ml/broker_wire.py
    2. Remove route at route_registry.py
    3. Remove the dedicated test file (tests/test_operator_trading_gate.py)
    4. Then delete this file.

═══════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)


# Collections + env key kept for back-compat with any operator UI
# that still queries them; nothing in this module reads or writes
# DB state for the gate's authorization decision anymore.
STATE_COLLECTION = "operator_trading_gate_state"
HISTORY_COLLECTION = "operator_trading_gate_history"
ENV_KEY = "OPERATOR_TRADING_AUTHORIZATION_ENABLED"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


# ── Test-mode shims (kept for back-compat with the gate's own tests) ──
# These never affect ``is_authorized``'s return value (which is always
# True). They exist solely so existing test code that calls them does
# not break.

_TEST_MODE_FORCE_AUTHORIZED: bool = True


def _force_test_mode_authorized(value: bool) -> None:  # noqa: ARG001 — kept for sig compat
    """No-op shim. Gate is always open under DOCTRINE V3."""
    return


_test_mode_disabled: bool = False


def _TEST_MODE_DISABLED_BY_FIXTURE() -> bool:  # noqa: N802
    return _test_mode_disabled


def _disable_test_mode_bypass(value: bool) -> None:  # noqa: ARG001 — kept for sig compat
    """No-op shim. Gate is always open under DOCTRINE V3."""
    return


# ── Read path (always-open) ────────────────────────────────────────


async def is_authorized(db) -> bool:  # noqa: ARG001 — kept for sig compat
    """DOCTRINE V3: permanently True.

    RISEDUAL no longer gates trade authorization locally. The Executor
    seat on Mission Control is the only place trades can originate,
    and the broker keys live exclusively on that host.
    """
    return True


async def get_status(db) -> dict[str, Any]:  # noqa: ARG001
    """Operator-facing snapshot — reflects the permanent-open doctrine."""
    return {
        "enabled": True,
        "by_operator": "doctrine_v3_permanently_open",
        "note": (
            "RISEDUAL is a headless brain. Trade authorization is "
            "delegated to Mission Control's Executor seat."
        ),
        "loaded_at": _utc_now().isoformat(),
    }


# ── Write path (no-op under V3) ────────────────────────────────────


async def set_authorized(
    db,  # noqa: ARG001
    *,
    enabled: bool,  # noqa: ARG001
    operator_id: str,  # noqa: ARG001
    note: str = "",  # noqa: ARG001
) -> dict[str, Any]:
    """No-op shim. The gate is permanently open; flipping it is a no-op.

    Kept callable so any admin UI that still POSTs to it gets a
    deterministic success response.
    """
    return {"ok": True, "state": await get_status(None)}


# ── Synthetic counterfactual receipt (no-op under V3) ──────────────


async def record_paused_synthetic(
    db,  # noqa: ARG001
    *,
    lane: str,  # noqa: ARG001
    symbol: str,  # noqa: ARG001
    decision: str,  # noqa: ARG001
    confidence: float = 0.0,  # noqa: ARG001
    extras: Optional[dict[str, Any]] = None,  # noqa: ARG001
) -> Optional[str]:
    """No-op shim. The gate is permanently open, so there's nothing
    to record as a counterfactual."""
    return None


async def gate_or_synthetic(
    db,  # noqa: ARG001
    *,
    lane: str,  # noqa: ARG001
    symbol: str,  # noqa: ARG001
    intended_decision: str,  # noqa: ARG001
    confidence: float = 0.0,  # noqa: ARG001
    extras: Optional[dict[str, Any]] = None,  # noqa: ARG001
) -> bool:
    """DOCTRINE V3: always returns True. Callers proceed unconditionally."""
    return True
