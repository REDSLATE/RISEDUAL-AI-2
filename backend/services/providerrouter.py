"""ProviderRouter — Health-aware provider router with error classification,
tiered cooldowns, latency tracking, MongoDB persistence, dynamic registration,
external heartbeats, and parallel orchestration with deadlines.

Static usage (admin health):
    ProviderRouter.snapshot()       → all lanes
    ProviderRouter.snapshot("ai")   → single lane

Instance usage (runtime failover):
    router = ProviderRouter("ai", get_ai_provider_pool(), db=db)
    result = await router.run(lambda provider: call_llm(provider))

Dynamic registration:
    ProviderRouter.register("ai", {"name": "new-model", "provider": "openai", "api_key": "sk-...", "model": "gpt-4.1", "priority": 5})
    ProviderRouter.deregister("ai", "new-model")

Parallel orchestration:
    results = await router.run_parallel(lambda p: call_model(p), deadline_ms=2000)
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)

# Class-level registry: lane → ProviderRouter instance
_registry: dict[str, "ProviderRouter"] = {}


class ProviderRouter:
    _state: dict[str, dict[str, dict]] = {}
    _dynamic_configs: dict[str, dict[str, dict]] = {}  # lane → name → provider config
    _lock = asyncio.Lock()

    def __init__(self, lane: str, providers: list[dict], db=None):
        self.lane = lane
        self.providers = sorted(providers, key=lambda x: x.get("priority", 999))
        self.db = db
        if lane not in self._state:
            self._state[lane] = {}
        for p in self.providers:
            if not p.get("api_key"):
                logger.warning(f"[ProviderRouter:{lane}] Skipping {p.get('name')} — no API key")
                continue
            self._state[lane].setdefault(p["name"], _new_health_entry())

        self.providers = [p for p in self.providers if p.get("api_key")]
        _registry[lane] = self

        if self.providers:
            logger.info(f"[ProviderRouter:{lane}] {len(self.providers)} providers: {[p['name'] for p in self.providers]}")
        else:
            logger.info(f"[ProviderRouter:{lane}] No providers configured")

    # ── Dynamic Registration ──

    @classmethod
    def register(cls, lane: str, provider: dict) -> dict:
        """Hot-register a new provider into a lane without restart."""
        name = provider.get("name")
        if not name or not provider.get("api_key"):
            return {"error": "name and api_key are required"}

        if lane not in cls._state:
            cls._state[lane] = {}
        if lane not in cls._dynamic_configs:
            cls._dynamic_configs[lane] = {}

        # Add to state
        cls._state[lane][name] = _new_health_entry()
        
        # Store provider config for list_models
        cls._dynamic_configs[lane][name] = provider

        # Add to instance if it exists
        instance = _registry.get(lane)
        if instance:
            existing_names = {p["name"] for p in instance.providers}
            if name not in existing_names:
                instance.providers.append(provider)
                instance.providers.sort(key=lambda x: x.get("priority", 999))

        logger.info(f"[ProviderRouter:{lane}] Registered: {name} (priority={provider.get('priority', 999)})")
        return {"registered": True, "lane": lane, "name": name}

    @classmethod
    def deregister(cls, lane: str, name: str) -> dict:
        """Remove a provider from a lane at runtime."""
        if lane in cls._state and name in cls._state[lane]:
            del cls._state[lane][name]
        
        # Remove from dynamic configs
        if lane in cls._dynamic_configs and name in cls._dynamic_configs[lane]:
            del cls._dynamic_configs[lane][name]

        instance = _registry.get(lane)
        if instance:
            instance.providers = [p for p in instance.providers if p["name"] != name]

        logger.info(f"[ProviderRouter:{lane}] Deregistered: {name}")
        return {"deregistered": True, "lane": lane, "name": name}

    @classmethod
    def heartbeat(cls, lane: str, name: str, status: str = "ok", latency_ms: float = 0, error_rate: float = 0) -> dict:
        """External health heartbeat — services self-report their status."""
        if lane not in cls._state or name not in cls._state[lane]:
            return {"accepted": False, "error": "provider not found"}

        state = cls._state[lane][name]
        now = datetime.now(timezone.utc)
        state["last_heartbeat"] = now

        if status == "ok":
            state["disabled"] = False
            state["cooldown_until"] = None
            state["consecutive_failures"] = 0
            if latency_ms > 0:
                if state["avg_latency_ms"]:
                    state["avg_latency_ms"] = round((state["avg_latency_ms"] * 0.7) + (latency_ms * 0.3), 2)
                else:
                    state["avg_latency_ms"] = round(latency_ms, 2)
        elif status == "degraded":
            state["consecutive_failures"] = max(state.get("consecutive_failures", 0), 1)
            state["cooldown_until"] = now + timedelta(seconds=30)
        elif status == "failed":
            state["disabled"] = True
            state["last_error"] = f"self-reported failure at {now.isoformat()}"
            state["last_error_type"] = "heartbeat_failed"

        return {"accepted": True, "lane": lane, "name": name, "status": status}

    @classmethod
    def list_models(cls, lane: Optional[str] = None) -> list[dict]:
        """List all registered providers with health across all or one lane."""
        results = []
        target_lanes = {lane: cls._state.get(lane, {})} if lane else cls._state

        for lane_name, providers in target_lanes.items():
            instance = _registry.get(lane_name)
            # Merge instance providers with dynamic configs
            provider_configs = {p["name"]: p for p in (instance.providers if instance else [])}
            # Also include dynamically registered providers
            dynamic_configs = cls._dynamic_configs.get(lane_name, {})
            for name, config in dynamic_configs.items():
                if name not in provider_configs:
                    provider_configs[name] = config

            for pname, state in providers.items():
                config = provider_configs.get(pname, {})
                entry = {
                    "lane": lane_name,
                    "name": pname,
                    "provider": config.get("provider", "unknown"),
                    "model": config.get("model", ""),
                    "priority": config.get("priority", 999),
                    "has_key": bool(config.get("api_key")),
                    "available": cls._is_available_static(lane_name, pname),
                }
                for k, v in state.items():
                    entry[k] = v.isoformat() if isinstance(v, datetime) else v
                results.append(entry)

        return results

    @staticmethod
    def _is_available_static(lane: str, name: str) -> bool:
        state = ProviderRouter._state.get(lane, {}).get(name, {})
        if state.get("disabled"):
            return False
        cd = state.get("cooldown_until")
        if cd and cd > datetime.now(timezone.utc):
            return False
        return True

    # ── Core routing (unchanged) ──

    def _classify_error(self, exc: Exception) -> str:
        msg = str(exc).lower()
        if "429" in msg or "rate limit" in msg or "quota" in msg:
            return "rate_limit"
        if "401" in msg or "403" in msg or "unauthorized" in msg or "invalid api key" in msg:
            return "auth"
        if "timeout" in msg:
            return "timeout"
        if "502" in msg or "503" in msg or "504" in msg:
            return "upstream"
        return "unknown"

    def _cooldown_seconds(self, error_type: str, consecutive_failures: int) -> int:
        if error_type == "auth":
            return 3600
        if error_type == "rate_limit":
            return min(300, 30 * max(1, consecutive_failures))
        if error_type in {"timeout", "upstream", "unknown"}:
            return min(180, 15 * max(1, consecutive_failures))
        return 60

    def _is_available(self, name: str) -> bool:
        return self._is_available_static(self.lane, name)

    def _score_provider(self, provider: dict) -> tuple:
        state = self._state[self.lane].get(provider["name"], {})
        available = self._is_available(provider["name"])
        return (
            0 if available else 1,
            state.get("consecutive_failures", 0),
            state.get("avg_latency_ms", 0) or 0,
            provider.get("priority", 999),
        )

    def get_ranked_providers(self) -> list[dict]:
        return sorted(self.providers, key=self._score_provider)

    async def _persist_health(self, provider_name: str):
        if self.db is None:
            return
        try:
            state = self._state[self.lane][provider_name]
            doc = {"lane": self.lane, "provider": provider_name, "updatedAt": datetime.now(timezone.utc)}
            for k, v in state.items():
                doc[k] = v.isoformat() if isinstance(v, datetime) else v
            await self.db.provider_health.update_one(
                {"lane": self.lane, "provider": provider_name}, {"$set": doc}, upsert=True,
            )
        except Exception as e:
            logger.warning(f"Failed to persist provider health for {provider_name}: {e}")

    async def mark_success(self, provider_name: str, latency_ms: float):
        async with self._lock:
            state = self._state[self.lane][provider_name]
            state["successes"] += 1
            state["consecutive_failures"] = 0
            state["last_success_at"] = datetime.now(timezone.utc)
            state["cooldown_until"] = None
            if state["avg_latency_ms"]:
                state["avg_latency_ms"] = round((state["avg_latency_ms"] * 0.7) + (latency_ms * 0.3), 2)
            else:
                state["avg_latency_ms"] = round(latency_ms, 2)
        await self._persist_health(provider_name)

    async def mark_failure(self, provider_name: str, exc: Exception):
        error_type = self._classify_error(exc)
        async with self._lock:
            state = self._state[self.lane][provider_name]
            state["failures"] += 1
            state["consecutive_failures"] += 1
            state["last_failure_at"] = datetime.now(timezone.utc)
            state["last_error"] = str(exc)[:500]
            state["last_error_type"] = error_type
            state["cooldown_until"] = datetime.now(timezone.utc) + timedelta(
                seconds=self._cooldown_seconds(error_type, state["consecutive_failures"])
            )
            if error_type == "auth":
                state["disabled"] = True
        await self._persist_health(provider_name)

    async def run(self, operation: Callable[[dict], Awaitable[Any]]) -> Any:
        """Execute operation with sequential failover across ranked providers."""
        ranked = self.get_ranked_providers()
        last_exc: Optional[Exception] = None

        for provider in ranked:
            name = provider["name"]
            if not self._is_available(name):
                continue
            started = datetime.now(timezone.utc)
            try:
                result = await operation(provider)
                latency_ms = (datetime.now(timezone.utc) - started).total_seconds() * 1000
                await self.mark_success(name, latency_ms)
                return {
                    "result": result,
                    "provider": {
                        "lane": self.lane, "name": provider["name"],
                        "type": provider.get("provider"), "model": provider.get("model"),
                    },
                }
            except Exception as exc:
                last_exc = exc
                logger.warning(f"Provider failure lane={self.lane} provider={name}: {exc}")
                await self.mark_failure(name, exc)
                continue

        if last_exc:
            raise last_exc
        raise RuntimeError(f"No providers configured for lane '{self.lane}'")

    # ── Parallel orchestration with deadline ──

    async def run_parallel(self, operation: Callable[[dict], Awaitable[Any]],
                           deadline_ms: int = 2000) -> list[dict]:
        """Call ALL available providers in parallel with a hard deadline.
        Returns a list of results — one per provider. Providers that miss the
        deadline are marked as 'timeout'. Failed providers are marked as 'error'.

        Returns: [{"name": "...", "status": "ok"|"timeout"|"error"|"unavailable", "result": ..., "latency_ms": ...}, ...]
        """
        results = []

        async def _call_one(provider: dict) -> dict:
            name = provider["name"]
            if not self._is_available(name):
                return {"name": name, "status": "unavailable", "result": None, "latency_ms": 0}
            started = datetime.now(timezone.utc)
            try:
                result = await asyncio.wait_for(
                    operation(provider),
                    timeout=deadline_ms / 1000,
                )
                latency_ms = (datetime.now(timezone.utc) - started).total_seconds() * 1000
                await self.mark_success(name, latency_ms)
                return {
                    "name": name, "status": "ok", "result": result,
                    "latency_ms": round(latency_ms, 1),
                    "provider": provider.get("provider"), "model": provider.get("model"),
                }
            except asyncio.TimeoutError:
                latency_ms = (datetime.now(timezone.utc) - started).total_seconds() * 1000
                await self.mark_failure(name, TimeoutError(f"Deadline {deadline_ms}ms exceeded"))
                return {"name": name, "status": "timeout", "result": None, "latency_ms": round(latency_ms, 1)}
            except Exception as exc:
                latency_ms = (datetime.now(timezone.utc) - started).total_seconds() * 1000
                await self.mark_failure(name, exc)
                return {"name": name, "status": "error", "result": None, "latency_ms": round(latency_ms, 1),
                        "error": str(exc)[:200]}

        tasks = [_call_one(p) for p in self.providers]
        results = await asyncio.gather(*tasks)
        return list(results)

    # ── Snapshot ──

    @classmethod
    def snapshot(cls, lane: Optional[str] = None):
        if lane:
            return cls._serialize_state(cls._state.get(lane, {}))
        result = {}
        for lane_name, lane_state in cls._state.items():
            result[lane_name] = cls._serialize_state(lane_state)
        return result

    @classmethod
    def _serialize_state(cls, lane_state: dict) -> dict:
        serialized = {}
        for provider_name, state in lane_state.items():
            entry = {}
            for k, v in state.items():
                entry[k] = v.isoformat() if isinstance(v, datetime) else v
            serialized[provider_name] = entry
        return serialized


def _new_health_entry() -> dict:
    return {
        "successes": 0,
        "failures": 0,
        "consecutive_failures": 0,
        "avg_latency_ms": 0.0,
        "last_error": None,
        "last_error_type": None,
        "last_success_at": None,
        "last_failure_at": None,
        "last_heartbeat": None,
        "cooldown_until": None,
        "disabled": False,
    }
