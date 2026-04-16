# risedual_core

Shared infrastructure library for `risedual-cli` and `risedual-ai`.

`risedual_core` is a pure library — it has no CLI entry points and no server code. It provides the HTTP market data clients, LLM provider abstraction, and tool registry framework that both surfaces import and build on.

---

## Install

```bash
pip install -e .
```

---

## Package layout

```
risedual_core/
├── pyproject.toml
├── README.md
└── risedual_core/
    ├── __init__.py          # __version__ = "0.1.0"
    ├── clients/
    │   ├── __init__.py
    │   ├── base.py          # BaseMarketClient (abstract, shared HTTP logic)
    │   ├── finnhub.py       # FinnhubClient
    │   ├── alpha_vantage.py # AlphaVantageClient
    │   ├── fred.py          # FredClient
    │   └── fmp.py           # FMPClient
    ├── llm/
    │   ├── __init__.py
    │   ├── base.py          # LLMProvider (abstract), LLMResponse, ToolCall
    │   ├── anthropic.py     # AnthropicLLM
    │   ├── openai.py        # OpenAILLM
    │   └── router.py        # LLMRouter (health-aware failover)
    └── tools/
        ├── __init__.py
        └── registry.py      # ToolRegistry + tool_registry singleton
```

---

## Usage

### Market data clients

All clients share a common `BaseMarketClient` base class that centralises
HTTP logic. Pass an optional shared `httpx.AsyncClient` for connection pooling,
or omit it to let each call manage its own transient client.

```python
import asyncio
from risedual_core.clients import FinnhubClient, AlphaVantageClient, FredClient, FMPClient

async def main() -> None:
    finnhub = FinnhubClient(api_key="YOUR_FINNHUB_KEY")
    quote = await finnhub.get_quote("AAPL")
    print(quote)  # {"current_price": 175.0, "change": 1.5, ...}

    profile = await finnhub.get_company_profile("AAPL")
    print(profile["name"])  # Apple Inc

    av = AlphaVantageClient(api_key="YOUR_AV_KEY")
    overview = await av.get_overview("AAPL")
    rsi = await av.get_rsi("AAPL", time_period=14)

    fred = FredClient(api_key="YOUR_FRED_KEY")
    gdp = await fred.get_series("GDP", limit=4)

    fmp = FMPClient(api_key="YOUR_FMP_KEY")
    income = await fmp.get_income_statement("AAPL", period="annual", limit=4)
    ratios = await fmp.get_key_ratios("AAPL")

asyncio.run(main())
```

### LLM router

`LLMRouter` manages multiple providers with automatic health-aware failover.
When the active provider fails twice consecutively it is marked unhealthy for
60 seconds and the next available provider is tried automatically.

```python
import asyncio
from risedual_core.llm import AnthropicLLM, OpenAILLM, LLMRouter

async def main() -> None:
    router = LLMRouter(
        providers=[
            AnthropicLLM(api_key="...", model="claude-3-5-sonnet-20241022"),
            OpenAILLM(api_key="...", model="gpt-4o"),
        ],
        default_provider_name="anthropic",
    )

    response = await router.chat(
        messages=[
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "What is the capital of France?"},
        ]
    )
    print(response.content)
    print(response.provider)  # "anthropic" or "openai" (whichever responded)

    # Switch the active provider manually
    router.set_active("openai")
    print(router.available_providers())  # ["anthropic", "openai"]

asyncio.run(main())
```

### Tool registry

The `tool_registry` singleton is the application-wide registry for AI-callable
tools. Register tools from surface-specific modules; the framework itself
contains no pre-registered tools.

```python
import asyncio
from risedual_core.tools import tool_registry
from risedual_core.llm import LLMRouter, AnthropicLLM

# Register a tool using the decorator factory
@tool_registry.register(
    name="get_weather",
    description="Get the current weather for a city.",
    parameters={
        "type": "object",
        "properties": {
            "city": {"type": "string", "description": "City name"},
        },
        "required": ["city"],
    },
)
async def _get_weather(city: str) -> dict:
    return {"city": city, "temperature_c": 22, "condition": "sunny"}


async def main() -> None:
    # Pass all registered schemas to the LLM
    schemas = tool_registry.get_schemas()

    router = LLMRouter(
        providers=[AnthropicLLM(api_key="...", model="claude-3-5-sonnet-20241022")],
        default_provider_name="anthropic",
    )

    response = await router.chat(
        messages=[{"role": "user", "content": "What is the weather in Paris?"}],
        tools=schemas,
    )

    # Dispatch any tool calls returned by the model
    for tc in response.tool_calls:
        result = await tool_registry.execute(tc.name, tc.arguments)
        print(result)

asyncio.run(main())
```

---

## Notes

- **Surface-specific tools** (CLI tools, web API tools) register against the
  shared `tool_registry` in their own modules, not here.
- All clients return empty dicts or lists on HTTP/network failure — no
  exceptions bubble up to callers.
- `LLMResponse` replaces the older `ProviderResponse` name from `risedual-cli`;
  update any imports accordingly.
