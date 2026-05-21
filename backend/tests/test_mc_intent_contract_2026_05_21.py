"""MC Prod Intent Contract — 2026-05-21.

Pins the exact wire shape Alpha sends to MC's ``POST /api/intents``:

* New top-level fields ``stack``, ``action``, ``lane``, ``rationale``,
  ``doctrine_snapshot`` — required by MC's gate chain.
* ``doctrine_snapshot.spread_bps`` MUST be populated (gate 7 fail-closes
  without it).
* Legacy ``side``/``notes``/``snapshot`` keys are still emitted for
  back-compat during the contract rollout.

Reference: operator contract message 2026-05-21 ("Brain → MC Intent
POST Contract"). MC re-mounts the wire ``doctrine_snapshot`` field
at ``shared_intents.<doc>.snapshot`` and ``doctrine_sidecars.<row>.snapshot``.
"""
from __future__ import annotations

import pytest

from sovereign.intent_bridge import _build_emission_kwargs
from sovereign.mc_client import build_intent_body, MCContractError


# ── _build_emission_kwargs: bridge stamps the new contract fields ──


def _consensus_receipt(action: str = "BUY", confidence_pct: float = 67.7,
                       symbol: str = "BTC/USD") -> dict:
    return {
        "symbol": symbol,
        "raw_action": action,
        "market_decision": action,
        "display_action": action,
        "final_confidence": confidence_pct,
        "summary": "BTC closing above 65k with elevated futures interest",
    }


def test_emission_kwargs_stamps_stack_and_lane_and_action_and_rationale():
    receipt = _consensus_receipt(action="BUY", symbol="BTC/USD")
    kw = _build_emission_kwargs(receipt, qty=1.0,
                                notes="BTC breakout above 65k with elevated tape")
    assert kw is not None
    # New prod contract fields stamped on every directional emission.
    assert kw["stack"] == "alpha"
    assert kw["action"] == "BUY"
    assert kw["lane"] == "crypto"  # _classify_lane on BTC/USD → CRYPTO → "crypto"
    assert kw["rationale"] == "BTC breakout above 65k with elevated tape"
    # Legacy fields preserved for back-compat.
    assert kw["side"] == "BUY"
    assert kw["notes"] == "BTC breakout above 65k with elevated tape"


def test_emission_kwargs_equity_symbol_gets_equity_lane():
    receipt = _consensus_receipt(action="BUY", symbol="NVDA")
    kw = _build_emission_kwargs(receipt, qty=1.0, notes="nvda momentum")
    assert kw is not None
    assert kw["lane"] == "equity"


def test_emission_kwargs_non_directional_returns_none():
    receipt = _consensus_receipt(action="HOLD")
    kw = _build_emission_kwargs(receipt, qty=1.0, notes="x")
    assert kw is None


# ── build_intent_body: wire shape conforms to MC prod contract ─────


def _base_body_kwargs() -> dict:
    return {
        "symbol": "BTC/USD",
        "side": "BUY",
        "qty": 1.0,
        "confidence": 0.74,
        "notes": "BTC breakout",
        # New contract fields:
        "stack": "alpha",
        "action": "BUY",
        "lane": "crypto",
        "rationale": "BTC closing above 65k with elevated futures interest",
        "doctrine_snapshot": {
            "spread_bps": 12,
            "relative_volume": 1.8,
            "gap_pct": 0.4,
            "has_news": True,
            "price": 65420.50,
            "volume": 58000000,
            "market_regime": "strong",
            "consecutive_losses": 0,
            "daily_pnl": 0.0,
        },
    }


def test_build_intent_body_emits_new_contract_top_level_fields():
    body = build_intent_body(**_base_body_kwargs())
    assert body["stack"] == "alpha"
    assert body["action"] == "BUY"
    assert body["lane"] == "crypto"
    assert body["rationale"].startswith("BTC closing above 65k")
    # The key MC reads on every gate-7 evaluation.
    assert body["doctrine_snapshot"]["spread_bps"] == 12
    assert body["doctrine_snapshot"]["price"] == 65420.50


def test_build_intent_body_back_compat_legacy_keys_still_present():
    body = build_intent_body(**_base_body_kwargs())
    # Legacy keys ride along — MC ignores unknowns but sibling
    # callers still reading the old shape don't break.
    assert body["side"] == "BUY"
    assert body["notes"] == "BTC breakout"


def test_build_intent_body_mirrors_snapshot_to_doctrine_snapshot_if_only_legacy_passed():
    """If a caller still hands us just ``snapshot=...``, the wire
    body must ALSO emit ``doctrine_snapshot`` (same dict). MC's gate
    7 reads ONLY doctrine_snapshot."""
    kwargs = _base_body_kwargs()
    kwargs.pop("doctrine_snapshot")
    kwargs["snapshot"] = {"spread_bps": 9, "price": 100.0}
    body = build_intent_body(**kwargs)
    assert body["snapshot"]["spread_bps"] == 9
    assert body["doctrine_snapshot"]["spread_bps"] == 9


def test_build_intent_body_rationale_capped_at_4000_chars():
    kwargs = _base_body_kwargs()
    kwargs["rationale"] = "x" * 5000
    body = build_intent_body(**kwargs)
    assert len(body["rationale"]) == 4000


def test_build_intent_body_rejects_invalid_action():
    kwargs = _base_body_kwargs()
    kwargs["action"] = "FROBNICATE"
    with pytest.raises(MCContractError):
        build_intent_body(**kwargs)


def test_build_intent_body_lowercases_lane():
    kwargs = _base_body_kwargs()
    kwargs["lane"] = "CRYPTO"
    body = build_intent_body(**kwargs)
    assert body["lane"] == "crypto"


def test_build_intent_body_omits_contract_fields_when_not_provided():
    """No regression: callers that don't pass the new fields still
    get a valid (legacy) body. This keeps tests that predate the
    contract rollout green."""
    body = build_intent_body(
        symbol="ETH/USD", side="BUY", qty=1.0, confidence=0.6, notes="legacy",
    )
    assert "stack" not in body
    assert "action" not in body
    assert "doctrine_snapshot" not in body
    assert body["side"] == "BUY"


# ── Snapshot completeness invariant ────────────────────────────────


def test_doctrine_snapshot_with_missing_spread_bps_is_caller_bug():
    """Callers MUST populate spread_bps before calling build_intent_body
    — gate 7 fails closed on missing values. This test documents the
    contract: the snapshot can carry sentinel values, but ``spread_bps``
    must be present as a key."""
    kwargs = _base_body_kwargs()
    kwargs["doctrine_snapshot"] = {"price": 100.0}  # no spread_bps
    body = build_intent_body(**kwargs)
    # We don't reject locally — MC will fail gate 7 and return a
    # diagnostic. Our contract is to ship whatever the caller built.
    # But the operator can grep ``doctrine_snapshot`` in the wire
    # log and see the missing key immediately.
    assert "spread_bps" not in body["doctrine_snapshot"]
