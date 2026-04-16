"""Secrets / KeyVault sub-package for risedual_core.

Re-exports the public API so callers only need::

    from risedual_core.secrets import KeyVault, SecretNotFoundError
"""

from __future__ import annotations

from risedual_core.secrets.keyvault import KeyVault, SecretNotFoundError

__all__ = ["KeyVault", "SecretNotFoundError"]
