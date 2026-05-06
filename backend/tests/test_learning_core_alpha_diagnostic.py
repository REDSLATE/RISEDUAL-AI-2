"""Tests for Patent M (Alpha) — diagnostic endpoint + Alpha
read-only invariants.

Pinned invariants:

* ``test_run_adversarial_decision_does_not_touch_learning_core`` —
  even with all learning-core env flags ENABLED, the adversarial
  decision payload must contain no ``learning_core`` and no
  ``learning_core_consumed`` keys. This is the Alpha hard rule:
  "no Strategist/Auditor/Council wire-up yet".

* ``test_to_dict_returns_pure_values`` — the diagnostic dump must
  be JSON-serialisable (no NumPy arrays, no class instances).

* ``test_to_dict_does_not_mutate_state`` — calling ``to_dict``
  must not move prototypes or change confusion-matrix counts.

* ``test_to_dict_exposes_rollout_relevant_fields`` — operator
  needs to see CACL dimensions, prototype counts, confusion
  matrix, regime cluster report, guardrail constants.
"""

from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest

os.environ.setdefault("REGIME_MEMORY_ENABLED", "true")
os.environ.setdefault("REGIME_MEMORY_MODE", "full_context")

import services.regime_memory_retrieval as _rmr  # noqa: E402

_rmr.REGIME_MEMORY_ENABLED = True
_rmr.REGIME_MEMORY_MODE = "full_context"

from services.learning_core_service import (  # noqa: E402
    get_core,
    reset_singleton_for_tests,
)


# ─── to_dict() purity + immutability ───────────────────────────


def test_to_dict_returns_pure_values():
    reset_singleton_for_tests()
    core = get_core()
    snapshot = core.to_dict()

    # Must be JSON-serialisable as-is.
    json.dumps(snapshot)

    # Top-level shape.
    assert "cacl" in snapshot
    assert "regime_memory" in snapshot
    assert "guardrails" in snapshot

    cacl = snapshot["cacl"]
    # No NumPy arrays.
    for k in ("input_dim", "embedding_dim", "n_prototypes",
              "proto_lr", "hard_neg_push", "temperature"):
        assert isinstance(cacl[k], (int, float))

    assert isinstance(cacl["confusion_matrix"], list)
    assert isinstance(cacl["n_seen_per_class"], list)


def test_to_dict_does_not_mutate_state():
    """Read-only contract — calling the dump must not change the
    core's internal state. This is the Alpha "no state mutation"
    rule, verified at the unit-test level."""
    reset_singleton_for_tests()
    core = get_core()

    # Run a small training step so there's actual state to compare.
    rng = np.random.default_rng(1)
    X = rng.normal(size=(4, 8))
    y = rng.integers(0, 3, size=4)
    core.train_batch(X, y)

    proto_before = core.embedding_core.P.copy()
    cm_before = core.embedding_core.confusion_matrix.copy()
    n_seen_before = core.embedding_core._n_seen.copy()

    # Call to_dict twice — must be stable.
    a = core.to_dict()
    b = core.to_dict()

    np.testing.assert_array_equal(core.embedding_core.P, proto_before)
    np.testing.assert_array_equal(
        core.embedding_core.confusion_matrix, cm_before,
    )
    np.testing.assert_array_equal(
        core.embedding_core._n_seen, n_seen_before,
    )
    assert a == b


def test_to_dict_exposes_rollout_relevant_fields():
    """Operator needs everything needed to audit the core's
    learning state without granting it execution authority."""
    reset_singleton_for_tests()
    core = get_core()
    snap = core.to_dict()

    # CACL dimensions and hyperparameters.
    cacl = snap["cacl"]
    assert "input_dim" in cacl
    assert "embedding_dim" in cacl
    assert "n_prototypes" in cacl
    assert "confusion_matrix" in cacl
    assert "row_sums" in cacl
    assert "col_sums" in cacl
    assert "n_seen_per_class" in cacl
    assert "hardest_confusion" in cacl

    # Regime memory cluster report.
    rmem = snap["regime_memory"]
    assert "total_regime_clusters" in rmem
    assert "total_pretell_clusters" in rmem
    assert "total_memories" in rmem

    # Guardrail constants — operator can verify caps haven't drifted.
    g = snap["guardrails"]
    assert g["max_confidence_delta"] == 0.15
    assert g["feature_dim"] == cacl["input_dim"]
    assert g["n_classes"] == cacl["n_prototypes"]


# ─── Alpha hard rule: no wire-up into adversarial flow ─────────


@pytest.mark.asyncio
async def test_run_adversarial_decision_does_not_touch_learning_core(
    monkeypatch,
):
    """Alpha hard rule.

    Even with EVERY learning-core env flag turned on, the
    adversarial decision payload must NOT contain any of:
      * ``learning_core`` (Phase 2 attachment)
      * ``learning_core_consumed`` (Phase 3 audit envelope)
      * any field whose name starts with ``learning_core``

    This regression test prevents an accidental re-wire from
    silently activating Patent M before its rollout step is
    approved.
    """
    monkeypatch.setenv("CRYPTO_ADVERSARIAL_ENABLED", "1")
    monkeypatch.setenv("ADVERSARIAL_PHASE", "shadow")
    monkeypatch.setenv("LEARNING_CORE_SHADOW_ENABLED", "true")
    monkeypatch.setenv("LEARNING_CORE_CONSUME_ENABLED", "true")

    from services.adversarial_core import run_adversarial_decision

    fake_db = MagicMock()
    fake_db.catalyst_snapshots.find_one = AsyncMock(return_value=None)

    signal = {
        "symbol": "AAPL",
        "confidence": 0.65,
        "expected_r": 0.02,
        "regime": "trending",
        "macro": {"vix": 18, "ten_year": 4.6, "two_year": 4.5},
        "strategist": {"indicators": {"rsi": 50, "momentum_5b": 0.1}},
    }
    out = await run_adversarial_decision(fake_db, signal)
    assert out is not None
    learning_core_keys = [k for k in out if str(k).startswith("learning_core")]
    assert learning_core_keys == [], (
        "Alpha rule violation: adversarial payload contains learning-"
        f"core field(s): {learning_core_keys}. Patent M must NOT be "
        "wired into the decision flow until each rollout step is "
        "explicitly approved."
    )


# ─── adapter check: diagnostic endpoint shape ──────────────────


def test_diagnostic_route_module_imports_cleanly():
    """The route module must be importable without side effects on
    the singleton (so a server restart doesn't accidentally build
    the core when learning-core is fully disabled)."""
    reset_singleton_for_tests()
    import importlib
    import services.learning_core_service as svc

    mod = importlib.import_module("routes.admin_learning_core")
    # Singleton should not have been built by import alone.
    assert svc._singleton is None
    # The route prefix matches the contract.
    assert mod.router.prefix == "/api/admin"
    assert any(
        getattr(r, "path", "") == "/api/admin/learning-core/diagnostic"
        for r in mod.router.routes
    )


def test_rollout_step_is_hardcoded_to_one():
    """The rollout step is a self-attesting badge — bumping it
    requires a code review that also wires the next layer. A
    runtime toggle would defeat the purpose."""
    import inspect
    import routes.admin_learning_core as mod
    src = inspect.getsource(mod.learning_core_diagnostic)
    assert '"rollout_step": 1' in src
    assert '"wired_into_decision_flow": False' in src


# ─── env-flag visibility ───────────────────────────────────────


def test_bool_env_helper_reads_each_flag(monkeypatch):
    from routes.admin_learning_core import _bool_env

    monkeypatch.setenv("LEARNING_CORE_SHADOW_ENABLED", "true")
    monkeypatch.setenv("LEARNING_CORE_CONSUME_ENABLED", "false")
    assert _bool_env("LEARNING_CORE_SHADOW_ENABLED") is True
    assert _bool_env("LEARNING_CORE_CONSUME_ENABLED") is False
    # Case-insensitive parse.
    monkeypatch.setenv("LEARNING_CORE_SHADOW_ENABLED", "TRUE")
    assert _bool_env("LEARNING_CORE_SHADOW_ENABLED") is True
