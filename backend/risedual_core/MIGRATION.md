# Migration Guide

## Install risedual_core

In each consumer project:
```bash
pip install -e /path/to/risedual_core
# or, if publishing to a private registry:
pip install risedual-core
```

## risedual-cli migration

The CLI shim files in `risedual_cli/clients/__init__.py`,
`risedual_cli/providers/__init__.py`, and `risedual_cli/tools/__init__.py`
already re-export from `risedual_core`.

The individual implementation files (`finnhub_client.py`, `anthropic_provider.py`, etc.)
can be deleted once `risedual_core` is installed — the `__init__.py` shims
replace them.

Until then, both exist in parallel and either import path works.

## risedual-ai (FastAPI backend) migration

1. Add `risedual-core` to `pyproject.toml` dependencies
2. Replace imports in service files:

```python
# Before
from app.clients.finnhub_client import FinnhubClient
# After
from risedual_core.clients import FinnhubClient
```

3. Use the FastAPI adapter helpers:
```python
from risedual_core.adapters.fastapi import make_llm_router_dependency, make_market_client_dependency
```

4. Update `app/models/prediction.py` to import enums from `risedual_core.schemas`
   (see `phase1/app/schemas/MIGRATION_NOTE.md`)

## Phase 1 (labeling system) migration

Same as FastAPI backend above. The `AuditRepository` and `LabelingService`
stay in `phase1/` — they are domain-specific and don't belong in `risedual_core`.
Only the shared schemas and clients move.
