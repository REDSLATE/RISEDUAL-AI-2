"""Market data API clients for risedual_core.

Re-exports all public client classes for convenient top-level imports::

    from risedual_core.clients import FinnhubClient, AlphaVantageClient, FredClient, FMPClient
"""
from __future__ import annotations

from risedual_core.clients.alpha_vantage import AlphaVantageClient
from risedual_core.clients.base import BaseMarketClient
from risedual_core.clients.finnhub import FinnhubClient
from risedual_core.clients.fmp import FMPClient
from risedual_core.clients.fred import FredClient

__all__ = [
    "BaseMarketClient",
    "FinnhubClient",
    "AlphaVantageClient",
    "FredClient",
    "FMPClient",
]
