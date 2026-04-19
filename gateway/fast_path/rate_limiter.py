"""
Token bucket rate limiter backed by Redis.

Uses a Lua script for atomic check-and-consume — prevents race conditions
when multiple gateway replicas serve the same agent.

Algorithm: token bucket
  - Bucket refills at `tokens_per_minute / 60` tokens per second
  - Burst capacity = `bucket_size`
  - On request: consume 1 token; if bucket empty → 429

Key schema (Redis DB0):
  rate:{tenant_id}:{agent_id}  →  Hash { tokens, last_refill_ts }
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from redis.asyncio import Redis

# Atomic Lua script: refill bucket based on elapsed time, then consume 1 token.
# Returns: [tokens_after_consume, tokens_max, reset_ts_unix]
_TOKEN_BUCKET_LUA = """
local key           = KEYS[1]
local now_ms        = tonumber(ARGV[1])
local refill_rate   = tonumber(ARGV[2])   -- tokens per ms
local bucket_max    = tonumber(ARGV[3])
local cost          = tonumber(ARGV[4])   -- tokens to consume (usually 1)

local bucket = redis.call('HMGET', key, 'tokens', 'last_refill_ts')
local tokens         = tonumber(bucket[1])
local last_refill_ts = tonumber(bucket[2])

if tokens == nil then
    -- First request: start with a full bucket
    tokens         = bucket_max
    last_refill_ts = now_ms
end

-- Refill
local elapsed = math.max(0, now_ms - last_refill_ts)
local refill  = math.floor(elapsed * refill_rate)
tokens        = math.min(bucket_max, tokens + refill)

-- Compute when bucket will have 1 token if empty
local reset_ts_ms = now_ms
if tokens < cost then
    local tokens_needed = cost - tokens
    reset_ts_ms = now_ms + math.ceil(tokens_needed / refill_rate)
end

if tokens >= cost then
    tokens = tokens - cost
    redis.call('HMSET', key, 'tokens', tokens, 'last_refill_ts', now_ms)
    redis.call('EXPIRE', key, 120)
    return {tokens, bucket_max, reset_ts_ms, 1}   -- 1 = allowed
else
    redis.call('HMSET', key, 'tokens', tokens, 'last_refill_ts', now_ms)
    redis.call('EXPIRE', key, 120)
    return {tokens, bucket_max, reset_ts_ms, 0}   -- 0 = denied
end
"""


@dataclass
class RateLimitResult:
    allowed: bool
    tokens_remaining: int
    tokens_max: int
    reset_at: datetime


async def check_rate_limit(
    redis: Redis,
    agent_id: str,
    tenant_id: str,
    tokens_per_minute: int,
    bucket_size: int,
    cost: int = 1,
) -> RateLimitResult:
    """
    Atomically check and consume a token for the given agent.
    Returns RateLimitResult.allowed=False if the bucket is exhausted.
    Falls back to ALLOW on Redis error (fail open).
    """
    key = f"rate:{tenant_id}:{agent_id}"
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    # tokens added per millisecond
    refill_rate = tokens_per_minute / 60_000.0

    try:
        result = await redis.eval(
            _TOKEN_BUCKET_LUA,
            1,          # number of keys
            key,
            now_ms,
            refill_rate,
            bucket_size,
            cost,
        )
        tokens_after, max_tokens, reset_ts_ms, allowed_int = result
        reset_dt = datetime.fromtimestamp(reset_ts_ms / 1000, tz=timezone.utc)
        return RateLimitResult(
            allowed=bool(allowed_int),
            tokens_remaining=int(tokens_after),
            tokens_max=int(max_tokens),
            reset_at=reset_dt,
        )
    except Exception:
        # Redis is down — fail open (governance layer must not block all agents)
        fallback_dt = datetime.now(timezone.utc)
        return RateLimitResult(
            allowed=True,
            tokens_remaining=-1,
            tokens_max=bucket_size,
            reset_at=fallback_dt,
        )
