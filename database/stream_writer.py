"""
Redis Streams producer — gateway writes audit events here.
Consumer worker (stream_consumer.py) reads and persists to Neo4j.

This decouples the gateway from Neo4j write latency.
If Redis is down, falls back to direct Neo4j write (slower but no data loss).
"""

from __future__ import annotations

import json

from database.audit_writer import AuditEvent, write_audit_event
from gateway.config import settings
from shared.redis_client import audit_stream_client


async def enqueue_audit_event(event: AuditEvent) -> None:
    """
    Write audit event to Redis Stream (primary path).
    Falls back to direct Neo4j write if Redis is unavailable.
    """
    payload = {
        "decision_id":   event.decision_id,
        "agent_id":      event.agent_id,
        "agent_name":    event.agent_name,
        "tenant_id":     event.tenant_id,
        "session_id":    event.session_id,
        "tool_name":     event.tool_name,
        "arguments_hash": event.arguments_hash,
        "verdict":       event.verdict,
        "reason":        event.reason,
        "path":          event.path,
        "rule_id":       event.rule_id or "",
        "latency_ms":    str(event.latency_ms),
        "policy_version": event.policy_version,
        "timestamp":     event.timestamp.isoformat(),
        "confidence":    str(event.confidence) if event.confidence is not None else "",
        "judge_tier":    str(event.judge_tier) if event.judge_tier is not None else "",
    }

    redis = audit_stream_client()
    try:
        await redis.xadd(
            settings.audit_stream_name,
            payload,
            maxlen=settings.audit_stream_maxlen,
            approximate=True,
        )
    except Exception:
        # Redis unavailable — write directly to Neo4j
        await write_audit_event(event)
    finally:
        await redis.aclose()
