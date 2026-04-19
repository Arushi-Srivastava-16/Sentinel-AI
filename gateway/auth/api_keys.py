"""
API key generation, hashing and validation.

Keys are formatted:  snl_<64 hex chars>
They are stored:     SHA-256(key) in Redis, never in plaintext
"""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime

from redis.asyncio import Redis

from gateway.config import settings

# Redis key pattern:  agent:key:{sha256_hex}  → JSON metadata
_KEY_PREFIX = "agent:key:"
# Reverse index:      agent:id:{agent_id}     → sha256_hex (for lookup by agent_id)
_ID_PREFIX = "agent:id:"


def generate_api_key() -> str:
    """Generate a new random API key.  Format: snl_<64 hex chars>"""
    return "snl_" + secrets.token_hex(32)


def hash_key(raw_key: str) -> str:
    """One-way SHA-256 hash of a raw API key."""
    return hashlib.sha256(raw_key.encode()).hexdigest()


async def store_api_key(
    redis: Redis,
    raw_key: str,
    agent_id: str,
    agent_name: str,
    policy_group: str,
    tenant_id: str,
) -> str:
    """Store a hashed API key in Redis. Returns the hash."""
    key_hash = hash_key(raw_key)
    metadata = {
        "agent_id": agent_id,
        "agent_name": agent_name,
        "policy_group": policy_group,
        "tenant_id": tenant_id,
        "created_at": datetime.utcnow().isoformat(),
        "is_admin": False,
    }
    await redis.set(f"{_KEY_PREFIX}{key_hash}", json.dumps(metadata))
    # Reverse index so we can look up by agent_id
    await redis.set(f"{_ID_PREFIX}{agent_id}", key_hash)
    return key_hash


async def store_admin_key(redis: Redis) -> None:
    """Seed the admin key from settings on startup."""
    raw_key = settings.sentinel_admin_key
    key_hash = hash_key(raw_key)
    existing = await redis.get(f"{_KEY_PREFIX}{key_hash}")
    if existing:
        return  # already seeded
    metadata = {
        "agent_id": "admin",
        "agent_name": "Sentinel Admin",
        "policy_group": "admin",
        "tenant_id": "system",
        "created_at": datetime.utcnow().isoformat(),
        "is_admin": True,
    }
    await redis.set(f"{_KEY_PREFIX}{key_hash}", json.dumps(metadata))


async def validate_api_key(redis: Redis, raw_key: str) -> dict | None:
    """
    Validate an API key.
    Returns the metadata dict if valid, None if invalid.
    """
    if not raw_key or not raw_key.startswith("snl_"):
        return None
    key_hash = hash_key(raw_key)
    value = await redis.get(f"{_KEY_PREFIX}{key_hash}")
    if value is None:
        return None
    return json.loads(value)


async def get_agent_key_hash(redis: Redis, agent_id: str) -> str | None:
    """Look up a key hash by agent_id (for rotation/deletion)."""
    return await redis.get(f"{_ID_PREFIX}{agent_id}")


async def revoke_api_key(redis: Redis, agent_id: str) -> bool:
    """Revoke all API keys for an agent. Returns True if anything was deleted."""
    key_hash = await get_agent_key_hash(redis, agent_id)
    if not key_hash:
        return False
    await redis.delete(f"{_KEY_PREFIX}{key_hash}")
    await redis.delete(f"{_ID_PREFIX}{agent_id}")
    return True
