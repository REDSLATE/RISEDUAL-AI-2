"""risedual_core — shared Python library for the risedual AI trading platform.

Provides the canonical ML layer, domain schemas, and adapter shims used by
both the ``risedual-cli`` command-line tool and the FastAPI backend service.

Sub-packages
------------
- :mod:`risedual_core.schemas` — Pydantic v2 domain models (canonical types)
- :mod:`risedual_core.ml`      — signal model, regime model, calibration
- :mod:`risedual_core.adapters`— CLI and FastAPI wiring helpers
"""

from __future__ import annotations

__version__ = "0.1.0"
__all__ = ["__version__"]
