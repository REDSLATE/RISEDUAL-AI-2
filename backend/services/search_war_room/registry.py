"""Search War Room — Provider Registry for dynamic adapter management.

Each adapter registers with its name, source_type, supported modes, and
whether it requires an API key. The orchestrator queries the registry
instead of hardcoding adapter imports.

Usage:
    from services.search_war_room.registry import get_enabled_providers, registry_status
    providers = get_enabled_providers("company", symbol="AAPL")
"""
import os
import logging
from dataclasses import dataclass, field
from typing import Callable, Awaitable, Optional

from services.search_war_room.schemas import EngineResult

logger = logging.getLogger(__name__)


@dataclass
class ProviderEntry:
    """A registered search provider."""
    name: str
    source_type: str
    modes: set  # which resolved modes this provider runs in
    run_fn: Callable  # async fn(query, symbol=None) -> EngineResult
    env_key: str = ""  # if set, provider is skipped when this env var is empty
    timeout: float = 8.0
    critical: bool = False  # if True, failure is flagged in the brief
    enabled: bool = True


_REGISTRY: dict[str, ProviderEntry] = {}


def register(entry: ProviderEntry):
    """Register a provider. Overwrites if name already exists."""
    _REGISTRY[entry.name] = entry


def deregister(name: str):
    """Remove a provider by name."""
    _REGISTRY.pop(name, None)


def enable(name: str):
    if name in _REGISTRY:
        _REGISTRY[name].enabled = True


def disable(name: str):
    if name in _REGISTRY:
        _REGISTRY[name].enabled = False


def get_enabled_providers(mode: str, symbol: str = None) -> list[ProviderEntry]:
    """Return providers that should fire for a given resolved mode."""
    result = []
    for p in _REGISTRY.values():
        if not p.enabled:
            continue
        if mode not in p.modes and "all" not in p.modes:
            continue
        # Skip providers that need an API key if the key is missing
        if p.env_key and not os.environ.get(p.env_key, ""):
            continue
        result.append(p)
    return result


def get_provider(name: str) -> Optional[ProviderEntry]:
    return _REGISTRY.get(name)


def registry_status() -> dict:
    """Return a summary for admin/status endpoints."""
    entries = []
    for p in _REGISTRY.values():
        has_key = True
        if p.env_key:
            has_key = bool(os.environ.get(p.env_key, ""))
        entries.append({
            "name": p.name,
            "source_type": p.source_type,
            "modes": sorted(p.modes),
            "timeout": p.timeout,
            "critical": p.critical,
            "enabled": p.enabled,
            "has_key": has_key,
            "active": p.enabled and has_key,
        })
    active = sum(1 for e in entries if e["active"])
    return {"total": len(entries), "active": active, "providers": entries}


# ─────────────────────────────────────────────
#  Auto-register all built-in adapters
# ─────────────────────────────────────────────

def _bootstrap():
    """Register all built-in adapters. Called once at import time."""
    from services.search_war_room.adapters import (
        ddg, wikipedia, fred, sec, yahoo, tavily,
        av_news, finnhub_news, stockfit,
    )

    _ALL_COMPANY = {"company", "filing", "news"}

    register(ProviderEntry(
        name="wikipedia", source_type="knowledge",
        modes={"company", "filing", "news", "macro", "general"},
        run_fn=lambda q, s=None: wikipedia.run(s or q),
        timeout=4.0, critical=True,
    ))
    register(ProviderEntry(
        name="sec", source_type="filing",
        modes={"company", "filing"},
        run_fn=sec.run,
        timeout=8.0, critical=True,
    ))
    register(ProviderEntry(
        name="stockfit", source_type="fundamental",
        modes={"company", "filing", "news"},
        run_fn=stockfit.run, env_key="STOCKFIT_API_KEY",
        timeout=12.0,
    ))
    register(ProviderEntry(
        name="tavily", source_type="search",
        modes={"company", "filing", "news", "macro", "general"},
        run_fn=lambda q, s=None: tavily.run(f"{s or q} stock analysis financial" if s else q),
        env_key="TAVILY_API_KEY", timeout=10.0,
    ))
    register(ProviderEntry(
        name="av_news", source_type="news",
        modes=_ALL_COMPANY,
        run_fn=av_news.run, env_key="ALPHA_VANTAGE_API_KEY",
        timeout=12.0,
    ))
    register(ProviderEntry(
        name="finnhub_news", source_type="news",
        modes=_ALL_COMPANY,
        run_fn=finnhub_news.run, env_key="FINNHUB_API_KEY",
        timeout=10.0,
    ))
    register(ProviderEntry(
        name="ddg", source_type="search",
        modes={"company", "filing", "news", "macro", "general"},
        run_fn=lambda q, s=None: ddg.run(q),
        timeout=7.0,
    ))
    register(ProviderEntry(
        name="ddg_news", source_type="news",
        modes={"company", "filing", "news"},
        run_fn=lambda q, s=None: ddg.run_news(f"{s or q} stock news" if s else q),
        timeout=7.0,
    ))
    register(ProviderEntry(
        name="yahoo", source_type="market",
        modes={"company", "news"},
        run_fn=yahoo.run,
        timeout=5.0,
    ))
    register(ProviderEntry(
        name="fred", source_type="macro",
        modes={"macro"},
        run_fn=lambda q, s=None: fred.run(q),
        env_key="FRED_API_KEYS", timeout=6.0, critical=True,
    ))

    logger.info(f"War Room registry: {len(_REGISTRY)} providers registered")


_bootstrap()
