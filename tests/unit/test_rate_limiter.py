"""
Unit tests for the Redis token bucket rate limiter.
Uses fakeredis — no real Redis required.
"""

import asyncio
from datetime import timezone

import fakeredis.aioredis as fakeredis
import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def redis():
    r = fakeredis.FakeRedis(decode_responses=True)
    yield r
    await r.aclose()


# ---------------------------------------------------------------------------
# Import the rate limiter (must happen after fakeredis is set up)
# ---------------------------------------------------------------------------
from gateway.fast_path.rate_limiter import check_rate_limit


class TestTokenBucket:
    @pytest.mark.asyncio
    async def test_first_request_allowed(self, redis):
        result = await check_rate_limit(
            redis, agent_id="agent_1", tenant_id="t1",
            tokens_per_minute=10, bucket_size=10,
        )
        assert result.allowed is True
        assert result.tokens_remaining == 9

    @pytest.mark.asyncio
    async def test_exhausted_bucket_blocks(self, redis):
        # Consume all 5 tokens
        for _ in range(5):
            r = await check_rate_limit(
                redis, agent_id="agent_x", tenant_id="t1",
                tokens_per_minute=5, bucket_size=5,
            )
            assert r.allowed is True

        # 6th request should be denied
        result = await check_rate_limit(
            redis, agent_id="agent_x", tenant_id="t1",
            tokens_per_minute=5, bucket_size=5,
        )
        assert result.allowed is False
        assert result.tokens_remaining == 0

    @pytest.mark.asyncio
    async def test_different_agents_isolated(self, redis):
        # Exhaust agent_a's bucket
        for _ in range(3):
            await check_rate_limit(redis, "agent_a", "t1", 3, 3)

        blocked = await check_rate_limit(redis, "agent_a", "t1", 3, 3)
        assert blocked.allowed is False

        # agent_b should still be fine
        ok = await check_rate_limit(redis, "agent_b", "t1", 3, 3)
        assert ok.allowed is True

    @pytest.mark.asyncio
    async def test_different_tenants_isolated(self, redis):
        for _ in range(2):
            await check_rate_limit(redis, "agent_1", "tenant_a", 2, 2)
        blocked = await check_rate_limit(redis, "agent_1", "tenant_a", 2, 2)
        assert blocked.allowed is False

        # Same agent_id in different tenant should be fine
        ok = await check_rate_limit(redis, "agent_1", "tenant_b", 2, 2)
        assert ok.allowed is True

    @pytest.mark.asyncio
    async def test_reset_at_is_in_future_when_blocked(self, redis):
        from datetime import datetime
        for _ in range(3):
            await check_rate_limit(redis, "agent_z", "t1", 3, 3)
        result = await check_rate_limit(redis, "agent_z", "t1", 3, 3)
        assert result.allowed is False
        now = datetime.now(timezone.utc)
        assert result.reset_at >= now

    @pytest.mark.asyncio
    async def test_fail_open_on_redis_error(self):
        """If Redis is completely broken, rate limiter allows requests through."""
        import fakeredis.aioredis as fakeredis2
        bad_redis = fakeredis2.FakeRedis(decode_responses=True)
        await bad_redis.aclose()   # Close it so all operations fail

        result = await check_rate_limit(bad_redis, "agent_1", "t1", 10, 10)
        assert result.allowed is True   # Fail open
