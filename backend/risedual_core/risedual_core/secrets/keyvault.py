"""Lightweight KeyVault for RISEDUAL secrets.

Design goals
------------
1. **Zero hard dependencies** — uses only stdlib (``cryptography`` is
   optional but strongly recommended for encryption at rest).
2. **Layered resolution** — secrets are resolved in priority order:

       env vars  →  encrypted vault file  →  plaintext vault file

3. **Encrypted at rest** — when ``cryptography`` is installed, the vault
   file is Fernet-encrypted using a master key derived from a passphrase or
   an explicit 32-byte key stored in ``RISEDUAL_VAULT_KEY``.
4. **Audit logging** — every read, write, and delete is written to a
   structured log entry (not the secret value itself).
5. **No circular imports** — this module has no imports from the rest of
   ``risedual_core``.

Usage
-----
::

    from risedual_core.secrets import KeyVault

    # Resolve from env or vault
    vault = KeyVault()
    api_key = vault.get("ALPACA_API_KEY")
    secret  = vault.get("ALPACA_SECRET_KEY")

    # Store a new secret (encrypted at rest)
    vault.set("ALPACA_API_KEY", "PK...")
    vault.set("ALPACA_SECRET_KEY", "SK...")

    # Delete a secret from the vault file
    vault.delete("ALPACA_API_KEY")

    # List stored keys (values never returned)
    print(vault.list_keys())

Environment variables
---------------------
``RISEDUAL_VAULT_PATH``
    Path to the vault file.  Defaults to ``~/.risedual/vault.enc`` (or
    ``vault.json`` without encryption).

``RISEDUAL_VAULT_KEY``
    32-byte URL-safe base64 Fernet key used to encrypt/decrypt the vault.
    Generate once with::

        python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

    Store this key in a secure location — NOT inside the vault file itself.
    If unset and ``cryptography`` is installed, the vault falls back to
    plaintext with a warning.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# ── Defaults ──────────────────────────────────────────────────────────────────

_DEFAULT_VAULT_DIR: Path = Path.home() / ".risedual"
_DEFAULT_VAULT_FILENAME_ENC: str = "vault.enc"
_DEFAULT_VAULT_FILENAME_PLAIN: str = "vault.json"
_VAULT_KEY_ENV: str = "RISEDUAL_VAULT_KEY"
_VAULT_PATH_ENV: str = "RISEDUAL_VAULT_PATH"


# ── Exceptions ────────────────────────────────────────────────────────────────

class SecretNotFoundError(KeyError):
    """Raised when a requested secret is not found in any resolution layer."""

    def __init__(self, key: str) -> None:
        super().__init__(f"Secret {key!r} not found in env or vault.")
        self.key = key


# ── Encryption helpers ────────────────────────────────────────────────────────

def _try_import_fernet() -> Any | None:
    """Return ``Fernet`` class if ``cryptography`` is installed, else ``None``."""
    try:
        from cryptography.fernet import Fernet  # type: ignore[import-untyped]
        return Fernet
    except ImportError:
        return None


def _make_fernet(key_b64: str) -> Any:
    """Construct a Fernet instance from a base64-encoded key string."""
    Fernet = _try_import_fernet()
    if Fernet is None:
        raise RuntimeError(
            "cryptography package not installed. "
            "Install with: pip install cryptography"
        )
    return Fernet(key_b64.encode() if isinstance(key_b64, str) else key_b64)


# ── KeyVault ──────────────────────────────────────────────────────────────────

class KeyVault:
    """Layered secret resolver with optional encryption at rest.

    Resolution order for :meth:`get`:

    1. Environment variable ``key``
    2. Encrypted vault file (if ``RISEDUAL_VAULT_KEY`` set and
       ``cryptography`` installed)
    3. Plaintext vault file (fallback — warns on first access)

    Parameters
    ----------
    vault_path:
        Explicit path to the vault file.  Overrides ``RISEDUAL_VAULT_PATH``.
    vault_key:
        Explicit Fernet key (base64 string).  Overrides
        ``RISEDUAL_VAULT_KEY``.  If ``None``, resolved from env.
    """

    def __init__(
        self,
        vault_path: str | Path | None = None,
        vault_key: str | None = None,
    ) -> None:
        self._vault_key: str | None = vault_key or os.getenv(_VAULT_KEY_ENV)
        self._encrypted: bool = self._vault_key is not None and _try_import_fernet() is not None

        if self._vault_key and _try_import_fernet() is None:
            log.warning(
                "[KeyVault] RISEDUAL_VAULT_KEY is set but 'cryptography' is not installed. "
                "Vault will be stored as plaintext. Install: pip install cryptography"
            )

        # Resolve vault path
        if vault_path:
            self._vault_path = Path(vault_path)
        elif os.getenv(_VAULT_PATH_ENV):
            self._vault_path = Path(os.environ[_VAULT_PATH_ENV])
        else:
            _dir = _DEFAULT_VAULT_DIR
            _dir.mkdir(parents=True, exist_ok=True)
            filename = (
                _DEFAULT_VAULT_FILENAME_ENC
                if self._encrypted
                else _DEFAULT_VAULT_FILENAME_PLAIN
            )
            self._vault_path = _dir / filename

        log.debug(
            "[KeyVault] Initialized — path=%s encrypted=%s",
            self._vault_path,
            self._encrypted,
        )

    # ── Internal I/O ─────────────────────────────────────────────────────────

    def _read_store(self) -> dict[str, str]:
        """Read and decode the vault file.  Returns empty dict if absent."""
        if not self._vault_path.exists():
            return {}
        try:
            raw = self._vault_path.read_bytes()
            if self._encrypted and self._vault_key:
                fernet = _make_fernet(self._vault_key)
                raw = fernet.decrypt(raw)
            store: dict[str, str] = json.loads(raw.decode("utf-8"))
            return store
        except Exception as exc:  # noqa: BLE001
            log.error("[KeyVault] Failed to read vault at %s: %s", self._vault_path, exc)
            return {}

    def _write_store(self, store: dict[str, str]) -> None:
        """Encode and write the vault file atomically."""
        payload = json.dumps(store, indent=2).encode("utf-8")
        if self._encrypted and self._vault_key:
            fernet = _make_fernet(self._vault_key)
            payload = fernet.encrypt(payload)
        # Atomic write via temp file
        tmp = self._vault_path.with_suffix(".tmp")
        try:
            self._vault_path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_bytes(payload)
            tmp.replace(self._vault_path)
        except Exception as exc:  # noqa: BLE001
            log.error("[KeyVault] Failed to write vault at %s: %s", self._vault_path, exc)
            raise

    # ── Public API ────────────────────────────────────────────────────────────

    def get(self, key: str, default: str | None = None) -> str:
        """Resolve a secret by *key*.

        Checks environment variables first, then the vault file.

        Parameters
        ----------
        key:
            Secret name (e.g. ``"ALPACA_API_KEY"``).
        default:
            Value returned if the secret is not found.  If ``None`` (the
            default) and the key is not found, raises :exc:`SecretNotFoundError`.

        Returns
        -------
        str
            The secret value.

        Raises
        ------
        SecretNotFoundError
            If the secret is not found and no *default* was provided.
        """
        # Layer 1 — environment variable
        env_val = os.getenv(key)
        if env_val is not None:
            log.debug("[KeyVault] Resolved %r from environment.", key)
            return env_val

        # Layer 2/3 — vault file
        store = self._read_store()
        if key in store:
            log.debug("[KeyVault] Resolved %r from vault file.", key)
            return store[key]

        # Not found
        if default is not None:
            log.debug("[KeyVault] %r not found — returning default.", key)
            return default

        log.warning("[KeyVault] Secret %r not found in env or vault.", key)
        raise SecretNotFoundError(key)

    def get_optional(self, key: str) -> str | None:
        """Like :meth:`get` but returns ``None`` instead of raising."""
        try:
            return self.get(key)
        except SecretNotFoundError:
            return None

    def set(self, key: str, value: str) -> None:
        """Store *value* under *key* in the vault file.

        Does **not** modify environment variables.

        Parameters
        ----------
        key:
            Secret name.
        value:
            Secret value.  Never logged.
        """
        store = self._read_store()
        store[key] = value
        self._write_store(store)
        log.info("[KeyVault] Stored secret %r in vault at %s.", key, self._vault_path)

    def delete(self, key: str) -> bool:
        """Remove *key* from the vault file.

        Parameters
        ----------
        key:
            Secret name to delete.

        Returns
        -------
        bool
            ``True`` if the key existed and was removed, ``False`` otherwise.
        """
        store = self._read_store()
        if key not in store:
            log.info("[KeyVault] delete(%r) — key not found, nothing to remove.", key)
            return False
        del store[key]
        self._write_store(store)
        log.info("[KeyVault] Deleted secret %r from vault.", key)
        return True

    def list_keys(self) -> list[str]:
        """Return a list of all key names stored in the vault file.

        Secret values are never included.

        Returns
        -------
        list[str]
            Sorted list of stored key names.
        """
        store = self._read_store()
        return sorted(store.keys())

    def has(self, key: str) -> bool:
        """Return ``True`` if *key* is resolvable (env or vault).

        Parameters
        ----------
        key:
            Secret name.
        """
        if os.getenv(key) is not None:
            return True
        return key in self._read_store()

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"KeyVault(path={self._vault_path!r}, "
            f"encrypted={self._encrypted}, "
            f"keys={self.list_keys()})"
        )
