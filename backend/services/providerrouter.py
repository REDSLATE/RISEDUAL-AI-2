"""ProviderRouter — Central registry of all provider pools with unified health snapshot.

Collects status from AI pool, Market Data pool, and key rotators into
a single admin-facing view organized by lane (ai, market_data, war_room_keys).
"""
import logging
from typing import Dict, List

logger = logging.getLogger(__name__)


class ProviderRouter:
    """Static registry — all pools register at import time."""

    @staticmethod
    def snapshot() -> List[Dict]:
        """Return health status for every provider lane."""
        lanes = []

        # Lane 1: AI Provider Pool
        try:
            from services.ai_pool import ai_pool
            pool_status = ai_pool.status()
            lanes.append({
                "lane": "ai",
                "label": "AI Provider Pool",
                "total": pool_status["total_providers"],
                "healthy": pool_status["healthy_providers"],
                "providers": pool_status["providers"],
            })
        except Exception as e:
            lanes.append({"lane": "ai", "label": "AI Provider Pool", "error": str(e)})

        # Lane 2: Market Data Provider Pool
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

        # Lane 3: War Room Key Rotators (FRED, Groq, OpenRouter)
        try:
            from services.search_war_room.adapters.fred import fred_rotator
            from services.key_rotator import KeyRotator
            import os

            rotators = [
                fred_rotator,
            ]
            # Build Groq/OpenRouter rotators on the fly for status
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
                    "success_rate": 100.0,
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
