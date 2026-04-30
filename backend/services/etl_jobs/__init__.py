"""ETL jobs registered with ``services.etl_registry``.

This package is imported at startup so the ``@register_etl_job``
decorators run and populate the registry. Adding a new source is
two steps:

1. Create a new module here (e.g. ``patent_uspto_weekly.py``)
2. Import it from this ``__init__`` so registration fires

Doing the import here (instead of relying on a glob) keeps the
registration list explicit and grep-friendly.
"""
from __future__ import annotations

# Importing for side effect — the ``@register_etl_job`` decorator
# in each module fires on import.
from . import quiver_congress_trades  # noqa: F401
from . import quiver_insiders         # noqa: F401
from . import quiver_lobbying         # noqa: F401
from . import quiver_gov_contracts    # noqa: F401

__all__ = [
    "quiver_congress_trades",
    "quiver_insiders",
    "quiver_lobbying",
    "quiver_gov_contracts",
]
