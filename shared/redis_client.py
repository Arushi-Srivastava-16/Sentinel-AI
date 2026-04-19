"""
Redis connection factory.

Four logical databases — each accessed via its own client:
  DB0  rate_limit_client()   — token bucket rate limiting
  DB1  audit_stream_client() — Redis Streams audit write queue
  DB2  websocket_client()    — Pub/Sub WebSocket fan-out
  DB3  judge_cache_client()  — judge response cache
"""

from __future__ import annotations

import redis.asyncio as aioredis
from redis.asyncio import Redis

from gateway.config import settings


def _make_client(db: int) -> Redis:
    return aioredis.Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        password=settings.redis_password or None,
        db=db,
        decode_responses=True,
        socket_connect_timeout=2,
        socket_timeout=2,
        retry_on_timeout=True,
    )


def rate_limit_client() -> Redis:
    return _make_client(settings.redis_db_rate_limit)


def audit_stream_client() -> Redis:
    return _make_client(settings.redis_db_audit_stream)


def websocket_client() -> Redis:
    return _make_client(settings.redis_db_websocket)


def judge_cache_client() -> Redis:
    return _make_client(settings.redis_db_judge_cache)


async def ping_redis() -> bool:
    """Health check — returns True if Redis DB0 is reachable."""
    try:
        client = rate_limit_client()
        await client.ping()
        await client.aclose()
        return True
    except Exception:
        return False
