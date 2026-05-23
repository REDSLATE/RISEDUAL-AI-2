"""ALPHA empty-contribution refusal — 2026-05-22 operator decree.

Pins the doctrine: the sidecar must NOT emit a contribution
envelope when ``recent_outcomes`` is empty. The brain checks in
(heartbeat keeps flowing) but the contribution slot stays silent
until the outcome bridge starts writing real rows.

MC's diagnostics screenshot showed 60 consecutive ``SOV-AUDIT
contribution • as executor • (empty payload)`` rows. This pin
prevents that regression.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

_SRC = Path("/app/backend/sovereign/sidecar.py").read_text(encoding="utf-8")


# ── Static authority firewall ──────────────────────────────────────


def test_sidecar_guards_empty_recent_outcomes():
    """The contribution-emit block must check that recent_outcomes
    is non-empty BEFORE calling post_contribution."""
    # The guard checks ``if not recent:`` and logs ABSTAIN.
    assert "recent = self.state.recent_outcomes(20)" in _SRC
    assert "if not recent:" in _SRC
    assert "ALPHA_ABSTAIN_CONTRIBUTION" in _SRC
    # The post_contribution call lives inside the else branch.
    guard_idx = _SRC.find("if not recent:")
    post_idx = _SRC.find("self.client.post_contribution(", guard_idx)
    else_idx = _SRC.find("else:", guard_idx)
    assert 0 < else_idx < post_idx, (
        "post_contribution must be gated by the empty-recent guard"
    )


def test_no_empty_contribution_is_emitted_in_tick():
    """The tick() method must NOT have a code path that calls
    post_contribution unconditionally."""
    # Find tick() body
    tick_start = _SRC.find("def tick(self) -> None:")
    next_def = _SRC.find("\n    def ", tick_start + 10)
    tick_body = _SRC[tick_start:next_def]
    # Every post_contribution call must be inside an ``else`` of
    # the empty-recent guard. We assert there's exactly one
    # post_contribution call in the tick body, and it lives after
    # the guard.
    pc_count = tick_body.count("self.client.post_contribution(")
    assert pc_count == 1, f"expected exactly 1 post_contribution call, got {pc_count}"
    guard_pos = tick_body.find("if not recent:")
    pc_pos = tick_body.find("self.client.post_contribution(")
    assert guard_pos < pc_pos, (
        "post_contribution must be downstream of the empty-recent guard"
    )


# ── Behavioural — empty outcomes ⇒ no MC call ──────────────────────


def test_tick_skips_post_contribution_when_outcomes_empty(monkeypatch):
    """Drive tick() with a state that has zero recent outcomes and
    confirm post_contribution is NOT called."""
    from sovereign.sidecar import SovereignSidecar
    from sovereign import outcome_inbox_client as oic

    # 2026-05-22 flake fix: sidecar.tick() does
    # ``from outcome_inbox_client import drain_pending_for_brain_sync``
    # via its sys.path-prefixed local imports, so the top-level
    # ``outcome_inbox_client`` module is a SEPARATE instance in
    # ``sys.modules`` from ``sovereign.outcome_inbox_client``.
    # Patching only the sovereign-prefixed copy let the real
    # drainer run against Mongo — and a concurrent
    # ``paper_trade_closer`` enqueueing a fresh row in production
    # would make this test fail intermittently. We patch BOTH
    # module instances so the hermeticity claim holds.
    import importlib
    top_oic = importlib.import_module("outcome_inbox_client")
    for mod in (oic, top_oic):
        monkeypatch.setattr(mod, "_get_db", lambda: None)
        monkeypatch.setattr(
            mod, "drain_pending_for_brain_sync",
            lambda brain, limit=20: [],
        )

    # Build sidecar with stubbed top-of-book + MC client
    fake_top = lambda sym: {
        "symbol": sym, "bid": 100.0, "ask": 100.1,
        "last": 100.05, "spread_bps": 10.0,
    }
    sc = SovereignSidecar(
        brain="alpha", mode="DTD",
        mc_base_url="https://example.invalid",
        runtime_token="t", symbols=["BTC/USD"],
        top_of_book_fn=fake_top,
    )
    # Force the MC client to a recording mock
    sc.client.post_stance = MagicMock(return_value={"ok": True})
    sc.client.post_contribution = MagicMock(return_value={"ok": True})
    sc.client.heartbeat = MagicMock(return_value={"ok": True})

    # Ensure local outcomes are empty
    sc.state._outcomes = []
    sc.tick()

    sc.client.post_contribution.assert_not_called()


def test_tick_emits_post_contribution_when_outcomes_present(monkeypatch):
    """Mirror test: with at least one recent outcome, the
    contribution DOES go out."""
    from sovereign.sidecar import SovereignSidecar
    from sovereign import outcome_inbox_client as oic
    from datetime import datetime, timezone

    # Inbox drainer stays hermetic — patch both module instances
    # (see flake fix in companion test above).
    import importlib
    top_oic = importlib.import_module("outcome_inbox_client")
    for mod in (oic, top_oic):
        monkeypatch.setattr(mod, "_get_db", lambda: None)
        monkeypatch.setattr(
            mod, "drain_pending_for_brain_sync",
            lambda brain, limit=20: [],
        )

    sc = SovereignSidecar(
        brain="alpha", mode="DTD",
        mc_base_url="https://example.invalid",
        runtime_token="t", symbols=["BTC/USD"],
        top_of_book_fn=lambda s: {
            "symbol": s, "bid": 100.0, "ask": 100.1,
            "last": 100.05, "spread_bps": 10.0,
        },
    )
    sc.client.post_stance = MagicMock(return_value={"ok": True})
    sc.client.post_contribution = MagicMock(return_value={"ok": True})
    sc.client.heartbeat = MagicMock(return_value={"ok": True})

    sc.state._outcomes = [{
        "symbol": "BTC/USD",
        "action": "BUY",
        "confidence": 0.62,
        "outcome": 1,
        "resolved_at": datetime.now(timezone.utc).isoformat(),
        "notional": 0.0,
    }]
    sc.tick()

    sc.client.post_contribution.assert_called_once()
    kwargs = sc.client.post_contribution.call_args.kwargs
    assert len(kwargs["recent_outcomes"]) == 1
    assert kwargs["recent_outcomes"][0]["symbol"] == "BTC/USD"
