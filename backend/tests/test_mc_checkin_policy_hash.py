"""
Policy-hash drift tripwire.

This test fires at build time. It catches the failure mode where MC
updates the `_POLICY` constitution dict but this brain ships with a
stale copy in `services/mc_checkin/__init__.py`.

If this test fails, the fix is:
    1. Open MC's `shared/runtime/platform_survival.py`
    2. Copy the current `_POLICY` dict (the one inside `policy_hash()`)
    3. Update `services/mc_checkin/__init__.py:_POLICY` to match
    4. Update `MC_CANONICAL_POLICY` below to match
    5. Re-run this test — it should pass
    6. Commit + redeploy

Doctrine note:
    Without this test, drift is only caught at runtime when MC flags
    the check-in verdict as `policy_drift`. That means the brain has
    already deployed with stale doctrine before anyone notices. This
    test moves the catch from "after deploy" to "in CI" — the doctrine
    is now an executable invariant.
"""
from __future__ import annotations

import hashlib
import json


# ─────────────────────────────────────────────────────────────────────
# CANONICAL POLICY — mirror of MC's `shared/runtime/platform_survival.py`.
#
# Source of truth: MC repo, file `shared/runtime/platform_survival.py`,
# function `policy_hash()`. This dict must match byte-for-byte (same
# keys, same values, same types — boolean True is NOT the same as 1).
#
# When MC updates its `_POLICY`, this dict MUST be updated in the same
# operator session. CI will fail loudly if they drift.
# ─────────────────────────────────────────────────────────────────────
MC_CANONICAL_POLICY = {
    "sidecars_may_execute": False,
    "mc_is_source_of_truth": True,
    "roadguard_required": True,
    "broker_requires_mc_receipt": True,
    "preview_is_not_prod": True,
}


def _canonical_hash() -> str:
    """Compute the sha256 of the canonical policy using MC's exact
    serialization scheme: JSON, sorted keys, no whitespace between
    tokens. Any deviation here will mismatch MC's `policy_hash()`."""
    raw = json.dumps(
        MC_CANONICAL_POLICY,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(raw).hexdigest()


def test_brain_policy_hash_matches_mc_canonical():
    """The brain's local `_policy_hash()` must produce the same sha256
    as MC's canonical `policy_hash()`. If this fails, the brain's
    `_POLICY` dict has drifted from MC's. See module docstring for
    fix instructions.
    """
    from services.mc_checkin import _policy_hash as brain_hash

    expected = _canonical_hash()
    actual = brain_hash()

    assert actual == expected, (
        f"\n  POLICY HASH DRIFT DETECTED\n"
        f"  brain's _policy_hash():   {actual}\n"
        f"  MC canonical hash:        {expected}\n"
        f"  Fix: sync services/mc_checkin/__init__.py:_POLICY against\n"
        f"  MC's shared/runtime/platform_survival.py:_POLICY, then\n"
        f"  update MC_CANONICAL_POLICY in this test file to match."
    )


def test_canonical_hash_is_well_known():
    """Lock-in test: the canonical hash is a known constant. If this
    value ever changes, MC has updated the constitution and every
    brain must be re-synced. Failing this test alone (without the
    first one also failing) means MC_CANONICAL_POLICY was edited but
    the brain's `_POLICY` wasn't — fix the brain side."""
    # Known sha256 of the canonical _POLICY dict as of 2026-02-19.
    # If MC updates _POLICY, this value WILL change — that's intentional;
    # it forces the operator to acknowledge the doctrine update.
    KNOWN_HASH = "2ac7d02164886f5c9c4a6339a605bf7be87b2bf2b532ea08681b5c29a6dcea25"

    assert _canonical_hash() == KNOWN_HASH, (
        f"\n  CANONICAL HASH DRIFT\n"
        f"  computed: {_canonical_hash()}\n"
        f"  expected: {KNOWN_HASH}\n"
        f"  Either MC_CANONICAL_POLICY in this file was edited (intentional\n"
        f"  doctrine update — bump KNOWN_HASH), or someone changed the JSON\n"
        f"  serialization (NOT intentional — revert)."
    )
