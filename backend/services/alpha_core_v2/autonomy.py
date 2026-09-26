"""Alpha-native autonomy layer ABOVE Core v2 (2026-06).

Reference: RISE_AI_KERNEL architecture. This layer decides WHETHER to run a
Core v2 cycle and WHETHER that cycle is live. It NEVER reimplements or bypasses
Core v2's broker/account/position/quote/size/idempotency/order/reconciliation
gates — it only invokes `CoreV2Engine.run_cycle(live=...)`. It is fully
MC-independent (no Mission Control calls).

Doctrine pins (load-bearing):
  * Provider/model role (SHADOW/ADVISOR/PRIMARY/OFFLINE) is metadata ONLY and
    NEVER grants trading authority.
  * Execution authority (OBSERVE/SHADOW/TOEHOLD/AUTONOMOUS/HALT) is a SEPARATE
    state machine, defaulting fail-safe to OBSERVE.
  * A live autonomous order requires ALL of: authority in {TOEHOLD, AUTONOMOUS}
    AND an explicit separate live arm (ALPHA_AUTONOMY_LIVE) AND Core v2 armed
    (ALPHA_CORE_V2). Authority alone is NOT sufficient.
  * HALT revokes new entries (no new cycles). Open positions still rely on
    Core v2 reconciliation + the existing protection policy.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from typing import Any, Awaitable, Callable, Optional

_TRUTHY = ("1", "true", "yes", "on")


class Authority(str, Enum):
    OBSERVE = "observe"        # watch only — dry cycles, never orders
    SHADOW = "shadow"          # shadow execution — dry cycles, receipts logged
    TOEHOLD = "toehold"        # tiny live positions — needs explicit live arm
    AUTONOMOUS = "autonomous"  # full autonomy — needs explicit live arm
    HALT = "halt"              # no new cycles / entries

    @classmethod
    def parse(cls, raw: Any) -> "Authority":
        v = (raw or "").strip().lower()
        for a in cls:
            if a.value == v:
                return a
        return cls.OBSERVE  # fail-safe default


class ProviderRole(str, Enum):
    OFFLINE = "offline"   # never used
    SHADOW = "shadow"     # logged, not consulted
    ADVISOR = "advisor"   # fallback / second opinion
    PRIMARY = "primary"   # preferred

    @classmethod
    def parse(cls, raw: Any, default: "ProviderRole") -> "ProviderRole":
        v = (raw or "").strip().lower()
        for r in cls:
            if r.value == v:
                return r
        return default


_LIVE_AUTHORITIES = (Authority.TOEHOLD, Authority.AUTONOMOUS)

# Provider roles are illustrative defaults; strictly separate from authority.
_PROVIDER_DEFAULTS = {
    "local": ProviderRole.SHADOW,
    "self_trained": ProviderRole.SHADOW,
    "anthropic": ProviderRole.PRIMARY,
    "openai": ProviderRole.ADVISOR,
    "gemini": ProviderRole.ADVISOR,
}


def _provider_roles() -> dict:
    out = {}
    for name, default in _PROVIDER_DEFAULTS.items():
        raw = os.environ.get(f"ALPHA_PROVIDER_ROLE_{name.upper()}")
        out[name] = ProviderRole.parse(raw, default).value
    return out


@dataclass
class AutonomyState:
    authority: Authority
    live_armed: bool
    core_v2_armed: bool
    provider_roles: dict

    def execute_live(self) -> bool:
        # The ONLY place that authorizes a live autonomous cycle. Provider role
        # is deliberately NOT consulted here.
        return (
            self.authority in _LIVE_AUTHORITIES
            and self.live_armed
            and self.core_v2_armed
        )

    def as_dict(self) -> dict:
        return {
            "authority": self.authority.value,
            "live_armed": self.live_armed,
            "core_v2_armed": self.core_v2_armed,
            "execute_live": self.execute_live(),
            "provider_roles": self.provider_roles,
        }


def load_state(cfg: Any = None) -> AutonomyState:
    if cfg is None:
        from services.alpha_core_v2.config import Config
        cfg = Config.load()
    return AutonomyState(
        authority=Authority.parse(os.environ.get("ALPHA_AUTONOMY_AUTHORITY")),
        live_armed=(os.environ.get("ALPHA_AUTONOMY_LIVE") or "").strip().lower()
        in _TRUTHY,
        core_v2_armed=bool(cfg.enabled),
        provider_roles=_provider_roles(),
    )


# Returns a ready CoreV2Engine (or None if broker unavailable). Injected so the
# controller never touches broker wiring directly — Core v2 stays authoritative.
EngineFactory = Callable[[], Awaitable[Optional[Any]]]


class AutonomyController:
    def __init__(self, engine_factory: EngineFactory, cfg: Any = None) -> None:
        self._engine_factory = engine_factory
        self._cfg = cfg

    async def tick(self) -> dict:
        state = load_state(self._cfg)
        base = state.as_dict()
        base["mc_independent"] = True

        if state.authority is Authority.HALT:
            return {**base, "ran": False, "reason": "HALT — no new cycles"}

        engine = await self._engine_factory()
        if engine is None:
            return {**base, "ran": False,
                    "reason": "engine unavailable (broker not connected)"}

        live = state.execute_live()  # False unless every doctrine gate holds
        result = await engine.run_cycle(live=live)
        return {**base, "ran": True, "live": live, "cycle": result.to_dict()}
