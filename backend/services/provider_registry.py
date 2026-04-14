"""Provider Registry — re-exports pool_config for cleaner import semantics."""
from services.pool_config import get_ai_provider_pool, get_market_data_provider_pool

__all__ = ["get_ai_provider_pool", "get_market_data_provider_pool"]
