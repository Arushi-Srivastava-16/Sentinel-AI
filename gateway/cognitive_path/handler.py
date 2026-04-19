"""
Cognitive path handler — async decision flow for LLM-judged tool calls.

Flow:
  1. Gateway receives tool call that needs cognitive evaluation
  2. Store "pending" decision in Redis with TTL
  3. Return HTTP 202 immediately with poll_url
  4. Background task runs judge cascade
  5. Result written to Redis + Neo4j
  6. WebSocket event published to dashboard

Agents poll GET /v1/decisions/{id} every 500ms.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog

from gateway.config import settings
from gateway.metrics import judge_calls_total, request_duration_seconds, requests_total
from gateway.models.requests import DecisionPath, DecisionResponse, Verdict
from judge.cascade import run_cascade
from judge.models import JudgeVerdict
from shared.redis_client import judge_cache_client, websocket_client

log = structlog.get_logger()

_PENDING_KEY_PREFIX = "decision:pending:"
_RESULT_KEY_PREFIX  = "decision:result:"
_PENDING_TTL_SECONDS = 120    # 2 minutes to complete or expire
_RESULT_TTL_SECONDS  = 3600   # keep results 1 hour for polling


async def store_pending(decision_id: str) -> None:
    """Mark a decision as pending in Redis."""
    redis = judge_cache_client()
    try:
        await redis.set(
            f"{_PENDING_KEY_PREFIX}{decision_id}",
            "pending",
            ex=_PENDING_TTL_SECONDS,
        )
    finally:
        await redis.aclose()


async def get_decision_result(decision_id: str) -> dict | None:
    """
    Poll for a completed decision.
    Returns None if still pending, the result dict if complete, raises if not found.
    """
    redis = judge_cache_client()
    try:
        # Check if still pending
        pending = await redis.get(f"{_PENDING_KEY_PREFIX}{decision_id}")
        if pending == "pending":
            return None   # still running

        # Check for completed result
        result_raw = await redis.get(f"{_RESULT_KEY_PREFIX}{decision_id}")
        if result_raw:
            return json.loads(result_raw)

        return {}   # Not found at all
    finally:
        await redis.aclose()


async def store_decision_result(decision_id: str, result: dict) -> None:
    """Store a completed decision result and clear the pending marker."""
    redis = judge_cache_client()
    try:
        await redis.set(
            f"{_RESULT_KEY_PREFIX}{decision_id}",
            json.dumps(result),
            ex=_RESULT_TTL_SECONDS,
        )
        await redis.delete(f"{_PENDING_KEY_PREFIX}{decision_id}")
    finally:
        await redis.aclose()


async def run_cognitive_evaluation(
    decision_id: str,
    agent_id: str,
    agent_name: str,
    tenant_id: str,
    session_id: str,
    tool_name: str,
    arguments: dict[str, Any],
    task_description: str,
    conversation_history: list[dict],
    source_documents: list[dict],
    policy_group: str,
    policy_version: str,
    arguments_hash: str,
) -> None:
    """
    Background task — runs judge cascade and stores result.
    Called via asyncio.create_task() so gateway responds immediately.
    """
    import time
    start = time.perf_counter_ns()

    log.info("cognitive_evaluation_started", decision_id=decision_id, tool=tool_name)

    try:
        judge_result = await run_cascade(
            agent_id=agent_id,
            tool_name=tool_name,
            arguments=arguments,
            task_description=task_description,
            conversation_history=conversation_history,
            source_documents=source_documents,
            policy_group=policy_group,
            policy_version=policy_version,
        )

        # Map JudgeVerdict → Verdict
        verdict_map = {
            JudgeVerdict.ALLOWED:      Verdict.ALLOWED,
            JudgeVerdict.BLOCKED:      Verdict.BLOCKED,
            JudgeVerdict.HUMAN_REVIEW: Verdict.HUMAN_REVIEW,
        }
        verdict = verdict_map[judge_result.verdict]
        latency_ms = (time.perf_counter_ns() - start) / 1_000_000

        result = {
            "decision_id": decision_id,
            "verdict": verdict.value,
            "reason": judge_result.reason,
            "path": DecisionPath.COGNITIVE.value,
            "latency_ms": round(latency_ms, 2),
            "policy_version": policy_version,
            "confidence": judge_result.confidence,
            "tier_used": judge_result.tier_used,
        }

        # Record metrics
        requests_total.labels(
            agent_id=agent_id,
            tool_name=tool_name,
            verdict=verdict.value,
            path="cognitive_path",
        ).inc()
        request_duration_seconds.labels(path="cognitive_path").observe(latency_ms / 1000)
        judge_calls_total.labels(
            tier=str(judge_result.tier_used),
            outcome=verdict.value,
        ).inc()

        # Persist result for polling
        await store_decision_result(decision_id, result)

        # Write to audit pipeline
        from database.audit_writer import AuditEvent
        from database.stream_writer import enqueue_audit_event

        event = AuditEvent(
            decision_id=decision_id,
            agent_id=agent_id,
            agent_name=agent_name,
            tenant_id=tenant_id,
            session_id=session_id,
            tool_name=tool_name,
            arguments_hash=arguments_hash,
            verdict=verdict.value,
            reason=judge_result.reason,
            path=DecisionPath.COGNITIVE.value,
            rule_id="cognitive_judge",
            latency_ms=latency_ms,
            policy_version=policy_version,
            timestamp=datetime.now(timezone.utc),
            confidence=judge_result.confidence,
            judge_tier=judge_result.tier_used,
        )
        await enqueue_audit_event(event)

        # Publish WebSocket event for dashboard
        await _publish_decision_event(result)

        log.info(
            "cognitive_evaluation_complete",
            decision_id=decision_id,
            verdict=verdict.value,
            latency_ms=latency_ms,
            tier=judge_result.tier_used,
        )

    except Exception as e:
        log.error("cognitive_evaluation_failed", decision_id=decision_id, error=str(e))
        # Store a HUMAN_REVIEW fallback so polling doesn't hang forever
        latency_ms = (time.perf_counter_ns() - start) / 1_000_000
        fallback = {
            "decision_id": decision_id,
            "verdict": Verdict.HUMAN_REVIEW.value,
            "reason": f"Evaluation failed: {str(e)[:200]}",
            "path": DecisionPath.COGNITIVE.value,
            "latency_ms": round(latency_ms, 2),
            "policy_version": policy_version,
            "confidence": 0.0,
            "tier_used": 0,
        }
        await store_decision_result(decision_id, fallback)
        await _publish_decision_event(fallback)


async def _publish_decision_event(result: dict) -> None:
    """Publish completed decision to WebSocket channel."""
    ws_redis = websocket_client()
    try:
        event = json.dumps({
            "event_type": "tool_call_decision",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **result,
        })
        await ws_redis.publish(settings.ws_pubsub_channel, event)
    except Exception:
        pass
    finally:
        await ws_redis.aclose()
