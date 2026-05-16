"""Brain identity helper — single source of truth for what this pod is.

REDEYE's `mc_sidecar` references ``mc_sovereign.brain_identity()`` in
its status payload so the operator can confirm at a glance which
brain a given pod is actually announcing itself as. We keep the
shape identical here.

Identity is read from environment variables (no hard-coding) so the
same image can be deployed as Alpha / Camaro / Chevelle / RedEye
just by flipping the env. Defaults are conservative.
"""
from __future__ import annotations

import os
from typing import Any

# Doctrinally the brain version maps to the persona version
# (Alpha 1.6, Camaro 1.3, Chevelle 1.3, RedEye 1.3). Read from env
# so the brain operator dashboard can show "Alpha 1.6" without
# the deployment having to bump code.
DEFAULT_BRAIN_NAME = "alpha"
DEFAULT_BRAIN_VERSION = "1.6"


def brain_identity() -> dict[str, Any]:
    """Return a small dict that uniquely identifies this brain pod.

    Stable shape contract — MC, the admin UI, and tests all rely on
    these exact keys. Add new keys; don't rename existing ones.
    """
    name = os.environ.get("ALPHA_BRAIN_NAME") or DEFAULT_BRAIN_NAME
    version = os.environ.get("ALPHA_BRAIN_VERSION") or DEFAULT_BRAIN_VERSION
    return {
        "name": name,
        "version": version,
        "mc_base_url": os.environ.get("MC_BASE_URL", ""),
        # The runtime token is intentionally NOT returned — operators
        # should be able to read this dict in logs and dashboards
        # without leaking the brain's MC credential.
        "has_runtime_token": bool(os.environ.get("ALPHA_INGEST_TOKEN", "")),
    }


__all__ = ["brain_identity", "DEFAULT_BRAIN_NAME", "DEFAULT_BRAIN_VERSION"]
