"""Supervisor Sidecar Lineage Stamp tests (2026-02-23).

Per MC operator's ask: every contribution from the supervisor-
run sidecar must carry enough lineage in ``notes`` that an
operator skimming the audit log can distinguish it from the
deleted in-process ``mc_sidecar._contribution_loop`` noise.

Required ``notes`` shape:
    sidecar v<version> · supervisor · contribution_id=<uuid12> · tick @ <ts>

This file pins:
    * ``notes`` includes ``sidecar v<X>`` (version stamp).
    * ``notes`` includes ``supervisor`` (transport tag).
    * ``notes`` includes ``contribution_id=<12 hex chars>`` per call.
    * Two consecutive ticks produce DIFFERENT contribution_ids
      (operator-distinguishable; no UUID re-use bug).
"""
from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ── 1) Static module structure ─────────────────────────────────────


def test_sidecar_module_exposes_lineage_constants():
    """The module must expose ``SIDECAR_VERSION`` + ``SIDECAR_TRANSPORT_TAG``
    so a future refactor doesn't accidentally drop the lineage stamp."""
    import importlib
    sys_path = Path("/app/backend/sovereign")
    import sys
    if str(sys_path) not in sys.path:
        sys.path.insert(0, str(sys_path))
    import sovereign.sidecar as sc  # noqa: E402
    assert hasattr(sc, "SIDECAR_VERSION")
    assert isinstance(sc.SIDECAR_VERSION, str) and sc.SIDECAR_VERSION
    assert sc.SIDECAR_TRANSPORT_TAG == "supervisor"


def test_sidecar_imports_uuid():
    """uuid is imported at module top — the lineage stamp uses uuid4."""
    src = Path("/app/backend/sovereign/sidecar.py").read_text(encoding="utf-8")
    assert "import uuid" in src
    assert "uuid.uuid4().hex" in src


def test_post_contribution_notes_carries_lineage_tags_static():
    """Static authority: the ``notes`` kwarg passed to
    ``post_contribution`` MUST include sidecar version, supervisor
    tag, and a contribution_id."""
    src = Path("/app/backend/sovereign/sidecar.py").read_text(encoding="utf-8")
    # The notes f-string must reference all three tags
    assert "sidecar v{SIDECAR_VERSION}" in src
    assert "{SIDECAR_TRANSPORT_TAG}" in src
    assert "contribution_id={contribution_id}" in src


# ── 2) Behavioural — tick() writes the right notes ─────────────────


def _patch_inbox_drainer(monkeypatch):
    """Helper: silence the outcome inbox drainer for hermetic ticks.
    Patches BOTH module instances (top-level + sovereign-prefixed)
    per the 2026-05-22 flake fix."""
    import importlib
    from sovereign import outcome_inbox_client as oic
    top_oic = importlib.import_module("outcome_inbox_client")
    for mod in (oic, top_oic):
        monkeypatch.setattr(mod, "_get_db", lambda: None)
        monkeypatch.setattr(
            mod, "drain_pending_for_brain_sync",
            lambda brain, limit=20: [],
        )


def test_tick_post_contribution_notes_carries_lineage(monkeypatch, tmp_path):
    """End-to-end: tick() with a seeded outcome produces a
    post_contribution call whose ``notes`` matches the lineage
    regex."""
    from sovereign.sidecar import SovereignSidecar
    _patch_inbox_drainer(monkeypatch)

    sc = SovereignSidecar(
        brain="alpha", mode="DTD",
        mc_base_url="https://example.invalid", runtime_token="t",
        symbols=["BTC/USD"], state_path=tmp_path / "s.json",
        top_of_book_fn=lambda s: {
            "symbol": s, "bid": 100.0, "ask": 100.1,
            "last": 100.05, "spread_bps": 10.0,
        },
    )
    # Seed an outcome so the empty-payload refusal doesn't fire.
    sc.state.add_outcome(
        symbol="BTC/USD", action="BUY", confidence=0.6,
        outcome=1, notional=0.0,
    )
    sc.client.post_contribution = MagicMock(return_value={"ok": True})
    sc.client.post_stance = MagicMock(return_value={"ok": True})
    sc.client.heartbeat = MagicMock(return_value={"ok": True})

    sc.tick()

    sc.client.post_contribution.assert_called_once()
    notes = sc.client.post_contribution.call_args.kwargs["notes"]
    # Lineage tags must be present.
    assert "sidecar v" in notes
    assert "supervisor" in notes
    assert "contribution_id=" in notes
    # The contribution_id is a 12-hex-char chunk.
    m = re.search(r"contribution_id=([0-9a-f]{12})", notes)
    assert m is not None, f"contribution_id missing or wrong shape: {notes!r}"


def test_consecutive_ticks_produce_distinct_contribution_ids(
    monkeypatch, tmp_path,
):
    """Each tick MUST stamp a fresh UUID — duplicate contribution_ids
    would defeat the operator's de-noising use case."""
    from sovereign.sidecar import SovereignSidecar
    _patch_inbox_drainer(monkeypatch)

    sc = SovereignSidecar(
        brain="alpha", mode="DTD",
        mc_base_url="https://example.invalid", runtime_token="t",
        symbols=["BTC/USD"], state_path=tmp_path / "s.json",
        top_of_book_fn=lambda s: {
            "symbol": s, "bid": 100.0, "ask": 100.1,
            "last": 100.05, "spread_bps": 10.0,
        },
    )
    sc.state.add_outcome(
        symbol="BTC/USD", action="BUY", confidence=0.6,
        outcome=1, notional=0.0,
    )
    sc.client.post_contribution = MagicMock(return_value={"ok": True})
    sc.client.post_stance = MagicMock(return_value={"ok": True})
    sc.client.heartbeat = MagicMock(return_value={"ok": True})

    sc.tick()
    sc.tick()
    assert sc.client.post_contribution.call_count == 2
    ids: list[str] = []
    for call in sc.client.post_contribution.call_args_list:
        notes = call.kwargs["notes"]
        m = re.search(r"contribution_id=([0-9a-f]{12})", notes)
        assert m is not None
        ids.append(m.group(1))
    assert ids[0] != ids[1], (
        "two consecutive ticks produced the same contribution_id — "
        "operator can't distinguish them in the MC audit log"
    )
