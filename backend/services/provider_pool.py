"""Provider Pool — Priority-based failover engine for any external service.

Accepts a pre-built list of provider dicts (from pool_config.py) or reads
from an env var. Tracks provider health and executes with automatic failover.
"""
import time
import logging
from typing import Any, Callable, List, Dict
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

COOLDOWN_SECONDS = 90
MAX_CONSECUTIVE_FAILURES = 3


@dataclass
class ProviderEntry:
    name: str
    provider: str
    api_key: str
    priority: int
    model: str = ""
    extra: dict = field(default_factory=dict)
    # Runtime state
    failures: int = 0
    last_failure: float = 0.0
    total_calls: int = 0
    total_successes: int = 0


class ProviderPool:
    """Generic priority-based provider pool with failover and health tracking."""

    def __init__(self, entries: List[Dict], name: str = "pool", cooldown: int = COOLDOWN_SECONDS):
        self.name = name
        self.cooldown = cooldown
        self.providers: list[ProviderEntry] = []
        self._load(entries)

    def _load(self, entries: List[Dict]):
        for entry in sorted(entries, key=lambda e: e.get("priority", 99)):
            api_key = entry.get("api_key", "")
            if not api_key:
                logger.warning(f"[ProviderPool:{self.name}] Skipping {entry.get('name')} — no API key")
                continue
            known_keys = {"name", "provider", "api_key", "model", "priority"}
            extra = {k: v for k, v in entry.items() if k not in known_keys}
            self.providers.append(ProviderEntry(
                name=entry["name"],
                provider=entry["provider"],
                api_key=api_key,
                model=entry.get("model", ""),
                priority=entry.get("priority", 99),
                extra=extra,
            ))
        if self.providers:
            logger.info(
                f"[ProviderPool:{self.name}] Loaded {len(self.providers)} providers: "
                f"{[p.name for p in self.providers]}"
            )
        else:
            logger.info(f"[ProviderPool:{self.name}] No providers configured")

    @property
    def available(self) -> bool:
        return len(self.providers) > 0

    def _is_healthy(self, provider: ProviderEntry) -> bool:
        if provider.failures < MAX_CONSECUTIVE_FAILURES:
            return True
        return (time.time() - provider.last_failure) > self.cooldown

    def get_healthy_providers(self) -> list[ProviderEntry]:
        """Return providers in priority order, healthy first."""
        healthy = [p for p in self.providers if self._is_healthy(p)]
        if not healthy:
            return sorted(self.providers, key=lambda p: p.last_failure)
        return healthy

    def mark_success(self, provider: ProviderEntry):
        provider.failures = 0
        provider.last_failure = 0.0
        provider.total_calls += 1
        provider.total_successes += 1

    def mark_failure(self, provider: ProviderEntry, error: str = ""):
        provider.failures += 1
        provider.last_failure = time.time()
        provider.total_calls += 1
        healthy_count = sum(1 for p in self.providers if self._is_healthy(p))
        logger.warning(
            f"[ProviderPool:{self.name}] {provider.name} failed ({provider.failures}x): {error[:100]}. "
            f"{healthy_count}/{len(self.providers)} healthy."
        )

    async def execute(self, fn: Callable, *args, **kwargs) -> Any:
        """Execute fn(provider, *args, **kwargs) with failover across providers.

        fn receives a ProviderEntry as its first argument and should raise on failure.
        """
        providers = self.get_healthy_providers()
        if not providers:
            raise RuntimeError(f"[ProviderPool:{self.name}] No providers available")

        last_error = None
        for provider in providers:
            try:
                result = await fn(provider, *args, **kwargs)
                self.mark_success(provider)
                return result
            except Exception as e:
                self.mark_failure(provider, str(e))
                last_error = e
                continue

        raise RuntimeError(
            f"[ProviderPool:{self.name}] All {len(providers)} providers exhausted. "
            f"Last error: {last_error}"
        )

    def status(self) -> dict:
        return {
            "pool": self.name,
            "total_providers": len(self.providers),
            "healthy_providers": sum(1 for p in self.providers if self._is_healthy(p)),
            "providers": [
                {
                    "name": p.name,
                    "provider": p.provider,
                    "model": p.model,
                    "priority": p.priority,
                    "healthy": self._is_healthy(p),
                    "failures": p.failures,
                    "total_calls": p.total_calls,
                    "success_rate": (
                        round(p.total_successes / p.total_calls * 100, 1)
                        if p.total_calls > 0 else 100.0
                    ),
                    "has_key": bool(p.api_key),
                }
                for p in self.providers
            ],
        }
