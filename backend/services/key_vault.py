"""Secure API Key Vault — encrypted storage for platform API keys.

Stores third-party API keys (OpenAI, Anthropic, Tavily, etc.) encrypted in
MongoDB with AES-256-GCM. Admin-only management via API.

Keys are loaded into os.environ at startup and can be rotated at runtime
without restarting the server.
"""
import os
import base64
import hashlib
import logging
import secrets
from datetime import datetime, timezone
from typing import Optional
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

logger = logging.getLogger(__name__)

COLLECTION = "api_key_vault"
VAULT_SECRET = os.environ.get("VAULT_SECRET", "")


def _get_cipher() -> Optional[AESGCM]:
    """Derive AES-256 key from VAULT_SECRET."""
    if not VAULT_SECRET:
        return None
    key = hashlib.sha256(VAULT_SECRET.encode()).digest()
    return AESGCM(key)


def _encrypt(plaintext: str) -> str:
    cipher = _get_cipher()
    if not cipher:
        return base64.b64encode(plaintext.encode()).decode()
    nonce = secrets.token_bytes(12)
    ct = cipher.encrypt(nonce, plaintext.encode(), None)
    return base64.b64encode(nonce + ct).decode()


def _decrypt(ciphertext: str) -> str:
    cipher = _get_cipher()
    raw = base64.b64decode(ciphertext)
    if not cipher:
        return raw.decode()
    nonce, ct = raw[:12], raw[12:]
    return cipher.decrypt(nonce, ct, None).decode()


class KeyVault:
    def __init__(self, db):
        self.db = db

    async def store(self, name: str, value: str, category: str = "general",
                    description: str = "", stored_by: str = "admin") -> dict:
        """Encrypt and store a platform API key."""
        encrypted = _encrypt(value)
        now = datetime.now(timezone.utc)
        await self.db[COLLECTION].update_one(
            {"name": name},
            {"$set": {
                "name": name,
                "encrypted_value": encrypted,
                "category": category,
                "description": description,
                "preview": value[:8] + "..." + value[-4:] if len(value) > 12 else "***",
                "stored_by": stored_by,
                "updated_at": now.isoformat(),
                "created_at": {"$ifNull": ["$created_at", now.isoformat()]},
            }},
            upsert=True,
        )
        return {"stored": True, "name": name, "preview": value[:8] + "..." + value[-4:]}

    async def retrieve(self, name: str) -> Optional[str]:
        """Retrieve and decrypt a platform API key."""
        doc = await self.db[COLLECTION].find_one({"name": name}, {"_id": 0})
        if not doc:
            return None
        try:
            return _decrypt(doc["encrypted_value"])
        except Exception as e:
            logger.error(f"Failed to decrypt vault key '{name}': {e}")
            return None

    async def list_keys(self) -> list:
        """List all stored keys (previews only, no values)."""
        cursor = self.db[COLLECTION].find({}, {"_id": 0, "encrypted_value": 0})
        return await cursor.to_list(length=100)

    async def delete(self, name: str) -> bool:
        result = await self.db[COLLECTION].delete_one({"name": name})
        return result.deleted_count > 0

    async def load_into_env(self):
        """Load all vault keys into os.environ (called at startup)."""
        cursor = self.db[COLLECTION].find({}, {"_id": 0})
        loaded = 0
        async for doc in cursor:
            try:
                value = _decrypt(doc["encrypted_value"])
                env_name = doc["name"]
                os.environ[env_name] = value
                loaded += 1
            except Exception as e:
                logger.warning(f"Failed to load vault key '{doc.get('name')}': {e}")
        if loaded:
            logger.info(f"Vault: loaded {loaded} keys into environment")
        return loaded

    async def inject_and_reload(self, name: str, value: str, category: str = "general",
                                description: str = "", stored_by: str = "admin"):
        """Store a key, inject into env, and reload affected provider pools."""
        await self.store(name, value, category, description, stored_by)
        os.environ[name] = value

        # Reload provider pools that depend on this key
        try:
            from services.pool_config import get_ai_provider_pool, get_market_data_provider_pool, get_email_provider_pool
            from services.providerrouter import ProviderRouter

            key_to_lane = {
                "OPENAI_API_KEY": ("ai", get_ai_provider_pool),
                "ANTHROPIC_API_KEY": ("ai", get_ai_provider_pool),
                "OPENROUTER_API_KEY": ("ai", get_ai_provider_pool),
                "ALPHA_VANTAGE_API_KEY": ("market_data", get_market_data_provider_pool),
                "ALPHAVANTAGEAPIKEY": ("market_data", get_market_data_provider_pool),
                "FINNHUB_API_KEY": ("market_data", get_market_data_provider_pool),
                "TWELVEDATA_API_KEY": ("market_data", get_market_data_provider_pool),
                "RESEND_API_KEY": ("email", get_email_provider_pool),
                "SENDGRID_API_KEY": ("email", get_email_provider_pool),
            }

            if name in key_to_lane:
                lane, pool_fn = key_to_lane[name]
                new_pool = pool_fn()
                for entry in new_pool:
                    ProviderRouter.register(lane, entry)
                logger.info(f"Vault: reloaded {lane} pool after storing {name}")

        except Exception as e:
            logger.warning(f"Pool reload after vault store failed: {e}")
