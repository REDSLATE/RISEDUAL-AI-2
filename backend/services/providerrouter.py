"""ProviderRouter — Central registry with both static health snapshot and instance-based failover execution.

Static usage (admin health):
    ProviderRouter.snapshot()  →  [{"lane": "ai", ...}, ...]

Instance usage (runtime failover):
    router = ProviderRouter("ai", get_ai_provider_pool())
    result = await router.run(lambda provider: call_llm(provider))
    # result = {"result": "...", "provider": "emergent-primary"}
"""
import time
import logging
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

COOLDOWN_SECONDS = 90
MAX_CONSECUTIVE_FAILURES = 3


class ProviderRouter:
    """Priority-based provider router with failover and health tracking."""

    # Class-level registry for snapshot()
    _instances: dict[str, "ProviderRouter"] = {}

    def __init__(self, lane: str, entries: List[Dict], db=None, cooldown: int = COOLDOWN_SECONDS):
        self.lane = lane
        self.db = db
        self.cooldown = cooldown
        self.providers: List[Dict] = []
        self._health: dict[str, dict] = {}

        for entry in sorted(entries, key=lambda e: e.get("priority", 99)):
            if not entry.get("api_key"):
                logger.warning(f"[ProviderRouter:{lane}] Skipping {entry.get('name')} — no API key")
                continue
            self.providers.append(entry)
            self._health[entry["name"]] = {
                "failures": 0,
                "last_failure": 0.0,
                "total_calls": 0,
                "total_successes": 0,
            }

        ProviderRouter._instances[lane] = self

        if self.providers:
            logger.info(f"[ProviderRouter:{lane}] {len(self.providers)} providers: {[p['name'] for p in self.providers]}")
        else:
            logger.info(f"[ProviderRouter:{lane}] No providers configured")

    def _is_healthy(self, name: str) -> bool:
        h = self._health.get(name, {})
        if h.get("failures", 0) < MAX_CONSECUTIVE_FAILURES:
            return True
        return (time.time() - h.get("last_failure", 0)) > self.cooldown

    def _mark_success(self, name: str):
        h = self._health[name]
        h["failures"] = 0
        h["last_failure"] = 0.0
        h["total_calls"] += 1
        h["total_successes"] += 1

    def _mark_failure(self, name: str, error: str = ""):
        h = self._health[name]
        h["failures"] += 1
        h["last_failure"] = time.time()
        h["total_calls"] += 1
        healthy_count = sum(1 for p in self.providers if self._is_healthy(p["name"]))
        logger.warning(
            f"[ProviderRouter:{self.lane}] {name} failed ({h['failures']}x): {error[:100]}. "
            f"{healthy_count}/{len(self.providers)} healthy."
        )

    async def run(self, fn: Callable) -> Dict[str, Any]:
        """Execute fn(provider_dict) with priority-based failover.

        Returns {"result": <return value>, "provider": "<provider name>"}.
        Raises RuntimeError if all providers fail.
        """
        healthy = [p for p in self.providers if self._is_healthy(p["name"])]
        if not healthy:
            healthy = sorted(self.providers, key=lambda p: self._health[p["name"]].get("last_failure", 0))

        if not healthy:
            raise RuntimeError(f"[ProviderRouter:{self.lane}] No providers available")

        last_error = None
        for provider in healthy:
            name = provider["name"]
            try:
                result = await fn(provider)
                self._mark_success(name)
                return {"result": result, "provider": name}
            except Exception as e:
                self._mark_failure(name, str(e))
                last_error = e
                continue

        raise RuntimeError(
            f"[ProviderRouter:{self.lane}] All {len(healthy)} providers exhausted. Last: {last_error}"
        )

    def status(self) -> Dict:
        return {
            "lane": self.lane,
            "total": len(self.providers),
            "healthy": sum(1 for p in self.providers if self._is_healthy(p["name"])),
            "providers": [
                {
                    "name": p["name"],
                    "provider": p.get("provider", ""),
                    "model": p.get("model", ""),
                    "priority": p.get("priority", 99),
                    "healthy": self._is_healthy(p["name"]),
                    "has_key": bool(p.get("api_key")),
                    **{k: v for k, v in self._health.get(p["name"], {}).items()},
                }
                for p in self.providers
            ],
        }

    # ── Static methods for admin snapshot ──

    @staticmethod
    def snapshot() -> List[Dict]:
        """Return health status for every registered provider lane."""
        lanes = []

        for lane_name, instance in ProviderRouter._instances.items():
            lanes.append({
                **instance.status(),
                "label": f"{lane_name.replace('_', ' ').title()} Provider Pool",
            })

        # Include market_data_pool (uses ProviderPool, not ProviderRouter)
        if "market_data" not in ProviderRouter._instances:
            try:
                from services.market_data_pool import market_pool
                pool_status = market_pool.status()
                lanes.append({
                    "lane": "market_data",
                    "label": "Market Data Provider Pool",
                    "total": pool_status["total_providers"],
                    "healthy": pool_status["healthy_providers"],
                    "providers": pool_status["providers"],
                })
            except Exception as e:
                lanes.append({"lane": "market_data", "label": "Market Data Provider Pool", "error": str(e)})

        # Also include key rotators if not already covered
        try:
            from services.search_war_room.adapters.fred import fred_rotator
            from services.key_rotator import KeyRotator

            rotators = [fred_rotator]
            groq = KeyRotator("GROQ_API_KEYS")
            openrouter = KeyRotator("OPENROUTER_API_KEYS")
            rotators.extend([groq, openrouter])

            rotator_providers = []
            for rot in rotators:
                s = rot.status()
                rotator_providers.append({
                    "name": s["provider"],
                    "provider": s["provider"].lower().replace("_api_keys", ""),
                    "model": "",
                    "priority": 0,
                    "healthy": s["healthy_keys"] > 0 or s["total_keys"] == 0,
                    "failures": 0,
                    "total_calls": 0,
                    "total_successes": 0,
                    "last_failure": 0.0,
                    "has_key": s["configured"],
                    "total_keys": s["total_keys"],
                    "healthy_keys": s["healthy_keys"],
                })

            total_configured = sum(1 for p in rotator_providers if p["has_key"])
            lanes.append({
                "lane": "war_room_keys",
                "label": "War Room Key Rotators",
                "total": len(rotator_providers),
                "healthy": total_configured,
                "providers": rotator_providers,
            })
        except Exception as e:
            lanes.append({"lane": "war_room_keys", "label": "War Room Key Rotators", "error": str(e)})

        return lanes
