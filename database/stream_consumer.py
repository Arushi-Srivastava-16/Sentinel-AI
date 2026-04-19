"""
Redis Streams consumer worker.
Reads from sentinel:audit:events stream, writes to Neo4j.
On failure, moves messages to DLQ stream for retry.

Run:  python -m database.stream_consumer
  or: make run-worker
"""

from __future__ import annotations

import asyncio
import json
import signal
import sys
from datetime import datetime, timezone

import structlog

from database.audit_writer import AuditEvent, write_audit_event
from gateway.config import settings
from shared.redis_client import audit_stream_client

log = structlog.get_logger()

_RUNNING = True
_BLOCK_MS = 2000      # Long-poll timeout when stream is empty
_BATCH_SIZE = 10      # Messages to process per iteration
_MAX_RETRIES = 3


async def _ensure_consumer_group(redis) -> None:
    try:
        await redis.xgroup_create(
            settings.audit_stream_name,
            settings.audit_consumer_group,
            id="0",
            mkstream=True,
        )
        log.info("consumer_group_created", group=settings.audit_consumer_group)
    except Exception as e:
        if "BUSYGROUP" in str(e):
            pass  # Already exists
        else:
            raise


def _parse_event(fields: dict) -> AuditEvent:
    return AuditEvent(
        decision_id=fields["decision_id"],
        agent_id=fields["agent_id"],
        agent_name=fields["agent_name"],
        tenant_id=fields["tenant_id"],
        session_id=fields["session_id"],
        tool_name=fields["tool_name"],
        arguments_hash=fields["arguments_hash"],
        verdict=fields["verdict"],
        reason=fields["reason"],
        path=fields["path"],
        rule_id=fields.get("rule_id", ""),
        latency_ms=float(fields.get("latency_ms", 0)),
        policy_version=fields.get("policy_version", "unknown"),
        timestamp=datetime.fromisoformat(fields["timestamp"]).replace(tzinfo=timezone.utc),
        confidence=float(fields["confidence"]) if fields.get("confidence") else None,
        judge_tier=int(fields["judge_tier"]) if fields.get("judge_tier") else None,
    )


async def _move_to_dlq(redis, msg_id: str, fields: dict, error: str) -> None:
    try:
        await redis.xadd(
            settings.audit_dlq_stream,
            {**fields, "_error": error, "_original_id": msg_id},
            maxlen=50_000,
        )
    except Exception as e:
        log.error("dlq_write_failed", msg_id=msg_id, error=str(e))


async def run_consumer() -> None:
    redis = audit_stream_client()
    await _ensure_consumer_group(redis)

    consumer_name = f"worker-{id(asyncio.get_event_loop())}"
    log.info("consumer_started", consumer=consumer_name, stream=settings.audit_stream_name)

    while _RUNNING:
        try:
            messages = await redis.xreadgroup(
                groupname=settings.audit_consumer_group,
                consumername=consumer_name,
                streams={settings.audit_stream_name: ">"},
                count=_BATCH_SIZE,
                block=_BLOCK_MS,
            )

            if not messages:
                continue

            for stream_name, entries in messages:
                for msg_id, fields in entries:
                    success = False
                    for attempt in range(1, _MAX_RETRIES + 1):
                        try:
                            event = _parse_event(fields)
                            success = await write_audit_event(event)
                            if success:
                                break
                        except Exception as e:
                            log.warning(
                                "audit_write_attempt_failed",
                                msg_id=msg_id,
                                attempt=attempt,
                                error=str(e),
                            )
                            if attempt < _MAX_RETRIES:
                                await asyncio.sleep(2 ** attempt)

                    if success:
                        await redis.xack(settings.audit_stream_name, settings.audit_consumer_group, msg_id)
                        log.debug("audit_event_written", msg_id=msg_id, decision_id=fields.get("decision_id"))
                    else:
                        await _move_to_dlq(redis, msg_id, fields, "max_retries_exceeded")
                        await redis.xack(settings.audit_stream_name, settings.audit_consumer_group, msg_id)
                        log.error("audit_event_dlq", msg_id=msg_id)

        except asyncio.CancelledError:
            break
        except Exception as e:
            log.error("consumer_loop_error", error=str(e))
            await asyncio.sleep(1)

    await redis.aclose()
    log.info("consumer_stopped")


def _handle_signal(*_) -> None:
    global _RUNNING
    _RUNNING = False


if __name__ == "__main__":
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)
    asyncio.run(run_consumer())
