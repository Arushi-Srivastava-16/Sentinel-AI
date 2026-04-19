"""
Integration tests — Fast Path components against real Redis.

Uses testcontainers-python to spin up a real Redis instance.
No mocks — exercises the actual Lua scripts, token bucket logic, and API key storage.

Run with:
    pytest tests/integration/test_fast_path_integration.py -v
    # (requires Docker)
"""

from __future__ import annotations

import os
import time

import pytest
import pytest_asyncio
import redis.asyncio as aioredis

# ---------------------------------------------------------------------------
# Container fixture — one Redis per module (fast startup, isolated)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def redis_container():
    """Spin up a real Redis 7 container for the duration of this module."""
    try:
        from testcontainers.redis import RedisContainer
    except ImportError:
        pytest.skip("testcontainers not installed — run: pip install testcontainers")

    with RedisContainer("redis:7.2-alpine") as container:
        yield container


@pytest.fixture(scope="module")
def redis_url(redis_container) -> str:
    host = redis_container.get_container_host_ip()
    port = redis_container.get_exposed_port(6379)
    return f"redis://{host}:{port}"


@pytest_asyncio.fixture
async def redis_client(redis_url):
    """Fresh async Redis client for each test, flushed before use."""
    client = aioredis.from_url(redis_url, decode_responses=True)
    await client.flushdb()
    yield client
    await client.aclose()


# ---------------------------------------------------------------------------
# Override settings to point at the test container
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def patch_redis_settings(redis_url, monkeypatch):
    """Redirect all gateway Redis clients to the test container."""
    import urllib.parse
    parsed = urllib.parse.urlparse(redis_url)
    monkeypatch.setenv("REDIS_HOST", parsed.hostname)
    monkeypatch.setenv("REDIS_PORT", str(parsed.port))
    monkeypatch.setenv("REDIS_PASSWORD", "")

    # Bust the lru_cache on settings so new env vars take effect
    from gateway.config import get_settings
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Rate Limiter tests
# ---------------------------------------------------------------------------

class TestRateLimiterIntegration:
    @pytest.mark.asyncio
    async def test_first_request_allowed(self, redis_client):
        from gateway.fast_path.rate_limiter import check_rate_limit
        result = await check_rate_limit(
            redis=redis_client,
            agent_id="agent_001",
            tenant_id="tenant_a",
            tokens_per_minute=10,
            bucket_size=10,
        )
        assert result.allowed is True
        assert result.tokens_remaining == 9

    @pytest.mark.asyncio
    async def test_bucket_exhaustion_blocks(self, redis_client):
        from gateway.fast_path.rate_limiter import check_rate_limit

        # Drain the bucket
        for _ in range(5):
            await check_rate_limit(
                redis=redis_client,
                agent_id="agent_drain",
                tenant_id="tenant_a",
                tokens_per_minute=5,
                bucket_size=5,
            )

        # Next request should be blocked
        result = await check_rate_limit(
            redis=redis_client,
            agent_id="agent_drain",
            tenant_id="tenant_a",
            tokens_per_minute=5,
            bucket_size=5,
        )
        assert result.allowed is False
        assert result.tokens_remaining == 0

    @pytest.mark.asyncio
    async def test_agent_isolation(self, redis_client):
        """Exhausting agent_a's quota must not affect agent_b."""
        from gateway.fast_path.rate_limiter import check_rate_limit

        # Drain agent_a
        for _ in range(3):
            await check_rate_limit(
                redis=redis_client,
                agent_id="agent_a",
                tenant_id="tenant_x",
                tokens_per_minute=3,
                bucket_size=3,
            )

        # agent_b should still be allowed
        result_b = await check_rate_limit(
            redis=redis_client,
            agent_id="agent_b",
            tenant_id="tenant_x",
            tokens_per_minute=3,
            bucket_size=3,
        )
        assert result_b.allowed is True

    @pytest.mark.asyncio
    async def test_tenant_isolation(self, redis_client):
        """Same agent_id in different tenants must have separate buckets."""
        from gateway.fast_path.rate_limiter import check_rate_limit

        # Drain in tenant_1
        for _ in range(3):
            await check_rate_limit(
                redis=redis_client,
                agent_id="shared_agent",
                tenant_id="tenant_1",
                tokens_per_minute=3,
                bucket_size=3,
            )

        # Same agent in tenant_2 should be unaffected
        result = await check_rate_limit(
            redis=redis_client,
            agent_id="shared_agent",
            tenant_id="tenant_2",
            tokens_per_minute=3,
            bucket_size=3,
        )
        assert result.allowed is True

    @pytest.mark.asyncio
    async def test_reset_at_is_in_future(self, redis_client):
        from gateway.fast_path.rate_limiter import check_rate_limit
        from datetime import datetime, timezone

        result = await check_rate_limit(
            redis=redis_client,
            agent_id="agent_time",
            tenant_id="tenant_t",
            tokens_per_minute=10,
            bucket_size=10,
        )
        assert result.reset_at > datetime.now(timezone.utc)

    @pytest.mark.asyncio
    async def test_fail_open_on_redis_error(self):
        """When Redis is unreachable, rate limiter must fail open (allow request)."""
        from gateway.fast_path.rate_limiter import check_rate_limit

        # Point at a port with nothing listening
        bad_client = aioredis.from_url("redis://localhost:19999", decode_responses=True)
        result = await check_rate_limit(
            redis=bad_client,
            agent_id="agent_fail",
            tenant_id="tenant_f",
            tokens_per_minute=10,
            bucket_size=10,
        )
        assert result.allowed is True, "Rate limiter must fail open when Redis is down"
        await bad_client.aclose()


# ---------------------------------------------------------------------------
# API Key store/validate tests
# ---------------------------------------------------------------------------

class TestApiKeyIntegration:
    @pytest.mark.asyncio
    async def test_store_and_validate_roundtrip(self, redis_client):
        from gateway.auth.api_keys import generate_api_key, store_api_key, validate_api_key

        key = generate_api_key()
        await store_api_key(
            redis=redis_client,
            api_key=key,
            agent_id="agent_kv_001",
            agent_name="test-agent",
            policy_group="financial",
            tenant_id="tenant_kv",
        )

        meta = await validate_api_key(redis_client, key)
        assert meta is not None
        assert meta["agent_id"] == "agent_kv_001"
        assert meta["agent_name"] == "test-agent"
        assert meta["policy_group"] == "financial"
        assert meta["tenant_id"] == "tenant_kv"
        assert meta["is_admin"] is False

    @pytest.mark.asyncio
    async def test_invalid_key_returns_none(self, redis_client):
        from gateway.auth.api_keys import validate_api_key

        result = await validate_api_key(redis_client, "snl_this_key_does_not_exist")
        assert result is None

    @pytest.mark.asyncio
    async def test_revoked_key_returns_none(self, redis_client):
        from gateway.auth.api_keys import generate_api_key, revoke_api_key, store_api_key, validate_api_key

        key = generate_api_key()
        await store_api_key(
            redis=redis_client,
            api_key=key,
            agent_id="agent_revoke",
            agent_name="revoke-test",
            policy_group="financial",
            tenant_id="tenant_rev",
        )

        # Validate works before revoke
        assert await validate_api_key(redis_client, key) is not None

        # Revoke and verify
        await revoke_api_key(redis_client, key)
        assert await validate_api_key(redis_client, key) is None

    @pytest.mark.asyncio
    async def test_key_format_starts_with_snl(self, redis_client):
        from gateway.auth.api_keys import generate_api_key

        for _ in range(5):
            key = generate_api_key()
            assert key.startswith("snl_"), f"Key {key!r} doesn't start with snl_"
            assert len(key) > 20


# ---------------------------------------------------------------------------
# Audit stream enqueue tests
# ---------------------------------------------------------------------------

class TestAuditStreamIntegration:
    @pytest.mark.asyncio
    async def test_enqueue_increases_stream_depth(self, redis_client):
        from datetime import datetime, timezone
        from database.audit_writer import AuditEvent
        from database.stream_writer import enqueue_audit_event

        event = AuditEvent(
            decision_id="dec_int_001",
            agent_id="agent_stream",
            agent_name="stream-test",
            tenant_id="tenant_s",
            session_id="sess_s_001",
            tool_name="read_file",
            arguments_hash="abc123",
            verdict="allowed",
            reason="Test event",
            path="fast_path",
            rule_id="test_rule",
            latency_ms=2.1,
            policy_version="financial-1.0.0",
            timestamp=datetime.now(timezone.utc),
        )

        # Patch to use test container client
        import unittest.mock as mock
        from gateway.config import settings

        with mock.patch("database.stream_writer.audit_stream_client", return_value=redis_client):
            await enqueue_audit_event(event)

        depth = await redis_client.xlen(settings.audit_stream_name)
        assert depth >= 1, f"Expected stream depth >= 1, got {depth}"

    @pytest.mark.asyncio
    async def test_enqueue_stores_required_fields(self, redis_client):
        from datetime import datetime, timezone
        from database.audit_writer import AuditEvent
        from database.stream_writer import enqueue_audit_event
        from gateway.config import settings
        import unittest.mock as mock

        event = AuditEvent(
            decision_id="dec_int_field_check",
            agent_id="agent_fields",
            agent_name="fields-test",
            tenant_id="tenant_f",
            session_id="sess_f_001",
            tool_name="execute_payment",
            arguments_hash="def456",
            verdict="blocked",
            reason="Payment threshold exceeded",
            path="fast_path",
            rule_id="threshold",
            latency_ms=5.0,
            policy_version="financial-1.0.0",
            timestamp=datetime.now(timezone.utc),
        )

        with mock.patch("database.stream_writer.audit_stream_client", return_value=redis_client):
            await enqueue_audit_event(event)

        # Read the last event from the stream
        messages = await redis_client.xrevrange(settings.audit_stream_name, "+", "-", count=1)
        assert messages, "No messages in stream"
        _, fields = messages[0]

        assert fields.get("decision_id") == "dec_int_field_check"
        assert fields.get("verdict") == "blocked"
        assert fields.get("tool_name") == "execute_payment"
        assert fields.get("tenant_id") == "tenant_f"
