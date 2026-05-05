"""Slow-suite tests — network-bound, real-LLM, real-broker, or
anything taking more than ~2s on a warm cache.

Default ``pytest`` skips this directory (see ``../pytest.ini``);
run explicitly with::

    pytest tests/slow

A test belongs here if it depends on:
  * the live HTTPS preview URL (cookie auth, real /me round-trips)
  * a real LLM call (Strategist / Auditor / Hypothesis pipelines)
  * a real broker sandbox (Alpaca, Kraken)
  * any external HTTP service that can't be mocked deterministically

Everything else stays in ``tests/`` at the root.
"""
