"""ProviderRouter — Health-aware provider router with error classification,
tiered cooldowns, latency tracking, MongoDB persistence, and admin snapshot.

Static usage (admin health):
    ProviderRouter.snapshot()       → all lanes
    ProviderRouter.snapshot("ai")   → single lane

Instance usage (runtime failover):
    router = ProviderRouter("ai", get_ai_provider_pool(), db=db)
    result = await router.run(lambda provider: call_llm(provider))
    # result = {"result": ..., "provider": {"lane": "ai", "name": "emergent-primary", ...}}
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class ProviderRouter:
    _state: Dict[str, Dict[str, dict]] = {}
    _lock = asyncio.Lock()

    def __init__(self, lane: str, providers: List[Dict], db=None):
        self.lane = lane
        self.providers = sorted(providers, key=lambda x: x.get("priority", 999))
        self.db = db
        if lane not in self._state:
            self._state[lane] = {}
        for p in self.providers:
            if not p.get("api_key"):
                logger.warning(f"[ProviderRouter:{lane}] Skipping {p.get('name')} — no API key")
                continue
            self._state[lane].setdefault(p["name"], {
                "successes": 0,
                "failures": 0,
                "consecutive_failures": 0,
                "avg_latency_ms": 0.0,
                "last_error": None,
                "last_error_type": None,
                "last_success_at": None,
                "last_failure_at": None,
                "cooldown_until": None,
                "disabled": False,
            })

        # Filter out providers without keys
        self.providers = [p for p in self.providers if p.get("api_key")]

        if self.providers:
            logger.info(f"[ProviderRouter:{lane}] {len(self.providers)} providers: {[p['name'] for p in self.providers]}")
        else:
            logger.info(f"[ProviderRouter:{lane}] No providers configured")

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
        state = self._state[self.lane].get(name, {})
        if state.get("disabled"):
            return False
        cd = state.get("cooldown_until")
        if cd and cd > datetime.now(timezone.utc):
            return False
        return True

    def _score_provider(self, provider: Dict) -> tuple:
        state = self._state[self.lane].get(provider["name"], {})
        available = self._is_available(provider["name"])
        return (
            0 if available else 1,
            state.get("consecutive_failures", 0),
            state.get("avg_latency_ms", 0) or 0,
            provider.get("priority", 999),
        )

    def get_ranked_providers(self) -> List[Dict]:
        return sorted(self.providers, key=self._score_provider)

    async def _persist_health(self, provider_name: str):
        if not self.db:
            return
        try:
            state = self._state[self.lane][provider_name]
            doc = {
                "lane": self.lane,
                "provider": provider_name,
                "updatedAt": datetime.now(timezone.utc),
            }
            for k, v in state.items():
                if isinstance(v, datetime):
                    doc[k] = v.isoformat()
                else:
                    doc[k] = v
            await self.db.provider_health.update_one(
                {"lane": self.lane, "provider": provider_name},
                {"$set": doc},
                upsert=True,
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

    async def run(self, operation: Callable[[Dict], Awaitable[Any]]) -> Any:
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
                        "lane": self.lane,
                        "name": provider["name"],
                        "type": provider.get("provider"),
                        "model": provider.get("model"),
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

    @classmethod
    def snapshot(cls, lane: Optional[str] = None):
        if lane:
            return cls._serialize_state(cls._state.get(lane, {}))
        result = {}
        for lane_name, lane_state in cls._state.items():
            result[lane_name] = cls._serialize_state(lane_state)
        return result

    @classmethod
    def _serialize_state(cls, lane_state: Dict) -> Dict:
        """Convert datetime objects to ISO strings for JSON serialization."""
        serialized = {}
        for provider_name, state in lane_state.items():
            entry = {}
            for k, v in state.items():
                if isinstance(v, datetime):
                    entry[k] = v.isoformat()
                else:
                    entry[k] = v
            serialized[provider_name] = entry
        return serialized
