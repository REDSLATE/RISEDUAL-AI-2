"""Shelly Full-Functionality Invariants.

Operator directive (2026-05-11): *"Let me make sure Shelly is
fully functional. Get rid of any other block that doesn't allow
her to be fully there."*

This test pins the env-flag posture after the 2026-05-11 audit
so a future change can't silently regress one of Shelly's lanes
back to default-OFF without an explicit edit + test update.

Doctrine:
  * The six learning-core flags must be ON for Shelly's perception
    → ingestion → persistence → rehydrate → influence loop to be
    closed.
  * ``OPERATOR_TRADING_AUTHORIZATION_ENABLED`` MUST stay OFF
    (golden rule — "no trades until I say so"). Shelly being
    fully functional and trading being locked are independent
    invariants; both are pinned here.
"""
from __future__ import annotations

from pathlib import Path


_ENV_PATH = Path(__file__).resolve().parents[1] / ".env"


def _read_env_flags() -> dict[str, str]:
    """Parse backend/.env into a {KEY: value} dict.

    Strict source-of-truth read — we deliberately don't go through
    ``os.environ`` because pytest runs may have its own env shadowing
    the file. The file IS the operator's contract.
    """
    out: dict[str, str] = {}
    if not _ENV_PATH.is_file():
        return out
    for line in _ENV_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip()
    return out


# Six Shelly-side flags that must all be "true" for full functionality.
# Order matches the perceive → ingest → persist → rehydrate → influence
# loop so a CI failure points at the first broken link.
_SHELLY_ON_FLAGS = (
    "LEARNING_CORE_SHADOW_ENABLED",
    "LEARNING_CORE_INGEST_ENABLED",
    "LEARNING_CORE_SHADOW_DELTA_LOG_ENABLED",
    "LEARNING_CORE_CONSUME_ENABLED",
    "LEARNING_CORE_PERSISTENCE_ENABLED",
    "LEARNING_CORE_REHYDRATE_ON_STARTUP",
)


def test_shelly_full_functionality_flags_are_on():
    """All six Shelly learning-core flags must be ``true`` in
    ``backend/.env``. If a future edit flips one off, this test
    pinpoints which lane regressed."""
    env = _read_env_flags()
    missing_or_off: list[str] = []
    for flag in _SHELLY_ON_FLAGS:
        if env.get(flag, "").lower() != "true":
            missing_or_off.append(f"{flag}={env.get(flag, '<unset>')}")
    assert not missing_or_off, (
        "Shelly is not fully functional — these flags are off or "
        "missing:\n  " + "\n  ".join(missing_or_off)
        + "\nOperator directive (2026-05-11) requires all six to be "
        "true. Edit backend/.env and re-run."
    )


def test_operator_trading_authorization_stays_locked():
    """Golden rule: trading authority is OFF. Shelly being fully
    functional must NEVER come with trading enabled in the same
    edit — that would conflate "she's learning" with "she's acting"
    and violate the operator's hard-stop on execution."""
    env = _read_env_flags()
    flag = env.get("OPERATOR_TRADING_AUTHORIZATION_ENABLED", "")
    assert flag.lower() == "false", (
        f"OPERATOR_TRADING_AUTHORIZATION_ENABLED must be 'false' "
        f"(currently {flag!r}). This is the golden rule — no trades, "
        f"paper or live, until the operator explicitly authorises. "
        f"If you intended to enable trading, this needs to be an "
        f"explicit operator-approved edit with a separate PR + audit "
        f"trail, not a silent flag flip."
    )
