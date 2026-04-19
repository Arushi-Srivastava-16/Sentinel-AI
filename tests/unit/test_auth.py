"""
Unit tests for API key generation, hashing, storage, and validation.
Uses fakeredis.
"""

import pytest
import pytest_asyncio
import fakeredis.aioredis as fakeredis

from gateway.auth.api_keys import (
    generate_api_key,
    hash_key,
    store_api_key,
    validate_api_key,
    revoke_api_key,
    get_agent_key_hash,
)


@pytest_asyncio.fixture
async def redis():
    r = fakeredis.FakeRedis(decode_responses=True)
    yield r
    await r.aclose()


class TestKeyGeneration:
    def test_format(self):
        key = generate_api_key()
        assert key.startswith("snl_")
        assert len(key) == 4 + 64   # "snl_" + 64 hex chars

    def test_uniqueness(self):
        keys = {generate_api_key() for _ in range(100)}
        assert len(keys) == 100

    def test_hash_is_deterministic(self):
        key = generate_api_key()
        assert hash_key(key) == hash_key(key)

    def test_different_keys_different_hashes(self):
        k1, k2 = generate_api_key(), generate_api_key()
        assert hash_key(k1) != hash_key(k2)


class TestKeyStorage:
    @pytest.mark.asyncio
    async def test_store_and_validate(self, redis):
        key = generate_api_key()
        await store_api_key(redis, key, "agent_1", "TestAgent", "financial", "tenant_a")
        meta = await validate_api_key(redis, key)
        assert meta is not None
        assert meta["agent_id"] == "agent_1"
        assert meta["policy_group"] == "financial"
        assert meta["tenant_id"] == "tenant_a"
        assert meta["is_admin"] is False

    @pytest.mark.asyncio
    async def test_invalid_key_returns_none(self, redis):
        result = await validate_api_key(redis, "snl_nonexistent_key_00000000")
        assert result is None

    @pytest.mark.asyncio
    async def test_wrong_prefix_returns_none(self, redis):
        result = await validate_api_key(redis, "sk-openai-notvalid")
        assert result is None

    @pytest.mark.asyncio
    async def test_empty_key_returns_none(self, redis):
        result = await validate_api_key(redis, "")
        assert result is None

    @pytest.mark.asyncio
    async def test_revoke_removes_key(self, redis):
        key = generate_api_key()
        await store_api_key(redis, key, "agent_2", "Agent2", "default", "tenant_a")
        assert await validate_api_key(redis, key) is not None

        revoked = await revoke_api_key(redis, "agent_2")
        assert revoked is True
        assert await validate_api_key(redis, key) is None

    @pytest.mark.asyncio
    async def test_revoke_nonexistent_returns_false(self, redis):
        result = await revoke_api_key(redis, "agent_ghost")
        assert result is False

    @pytest.mark.asyncio
    async def test_get_agent_key_hash(self, redis):
        key = generate_api_key()
        await store_api_key(redis, key, "agent_3", "Agent3", "financial", "t1")
        stored_hash = await get_agent_key_hash(redis, "agent_3")
        assert stored_hash == hash_key(key)
