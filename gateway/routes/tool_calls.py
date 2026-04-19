"""
Core governance route — POST /v1/tool-calls

Flow:
  1. Auth (middleware — already done by the time we get here)
  2. Rate limit check
  3. Classifier: fast_path or cognitive_path?
  4. Fast path: denylist → allowlist → threshold → regex
     - If verdict determined → respond immediately (sync)
     - If cognitive_check escalation → fall through to step 5
  5. Cognitive path: store pending, return 202, background task runs judge cascade
  6. Audit event written async to Redis Stream → Neo4j
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status

from database.audit_writer import AuditEvent, write_audit_event
from database.stream_writer import enqueue_audit_event
from gateway.classifier.heuristics import PathDecision, classify
from gateway.cognitive_path.handler import run_cognitive_evaluation, store_pending
from gateway.config import settings
from gateway.fast_path.rate_limiter import RateLimitResult, check_rate_limit
from gateway.fast_path.router import FastPathResult, evaluate_fast_path
from gateway.metrics import (
    audit_dlq_depth,
    rate_limit_hits_total,
    request_duration_seconds,
    requests_total,
)
from gateway.middleware.auth import AgentContext, require_agent
from gateway.models.requests import (
    DecisionPath,
    DecisionResponse,
    PendingDecisionResponse,
    RateLimitInfo,
    ToolCallRequest,
    Verdict,
)
from policies.loader import get_fast_path_rules_for_group
from shared.redis_client import rate_limit_client, websocket_client

router = APIRouter(prefix="/v1", tags=["Tool Calls"])


def _args_hash(arguments: dict) -> str:
    serialised = json.dumps(arguments, sort_keys=True)
    return hashlib.sha256(serialised.encode()).hexdigest()[:16]


@router.post(
    "/tool-calls",
    response_model=DecisionResponse | PendingDecisionResponse,
    status_code=200,
)
async def evaluate_tool_call(
    body: ToolCallRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    agent: AgentContext = Depends(require_agent),
) -> DecisionResponse | PendingDecisionResponse:
    start_ns = time.perf_counter_ns()
    decision_id = f"dec_{uuid.uuid4().hex[:10]}"
    session_id = body.session_id or f"sess_{uuid.uuid4().hex[:8]}"

    # ------------------------------------------------------------------
    # 1. Rate limit
    # ------------------------------------------------------------------
    rl_redis = rate_limit_client()
    rl: RateLimitResult = await check_rate_limit(
        redis=rl_redis,
        agent_id=agent.agent_id,
        tenant_id=agent.tenant_id,
        tokens_per_minute=settings.rate_limit_tokens_per_minute,
        bucket_size=settings.rate_limit_bucket_size,
    )
    await rl_redis.aclose()

    if not rl.allowed:
        latency_ms = (time.perf_counter_ns() - start_ns) / 1_000_000
        rate_limit_hits_total.labels(agent_id=agent.agent_id).inc()
        requests_total.labels(
            agent_id=agent.agent_id,
            tool_name=body.tool_name,
            verdict="blocked",
            path="fast_path",
        ).inc()
        request_duration_seconds.labels(path="fast_path").observe(latency_ms / 1000)
        # Fire-and-forget audit
        background_tasks.add_task(
            _log_audit,
            decision_id=decision_id,
            agent=agent,
            body=body,
            session_id=session_id,
            verdict=Verdict.BLOCKED,
            reason="Rate limit exceeded",
            path=DecisionPath.FAST,
            rule_id="rate_limit",
            latency_ms=latency_ms,
            policy_version="n/a",
        )
        background_tasks.add_task(
            _publish_decision_event,
            decision_id=decision_id,
            agent_id=agent.agent_id,
            tool_name=body.tool_name,
            verdict=Verdict.BLOCKED,
            reason="Rate limit exceeded",
            path=DecisionPath.FAST,
            latency_ms=latency_ms,
            policy_version="n/a",
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            headers={"X-RateLimit-Reset": rl.reset_at.isoformat()},
            detail={
                "error": "rate_limit_exceeded",
                "message": "Token bucket exhausted.",
                "reset_at": rl.reset_at.isoformat(),
            },
        )

    # ------------------------------------------------------------------
    # 2. Classify: fast_path or cognitive_path?
    # ------------------------------------------------------------------
    context_dict = {"task_description": body.context.task_description}
    path_decision = classify(body.tool_name, body.arguments, context_dict)

    # ------------------------------------------------------------------
    # 3. Load active policy rules and run fast path checks
    #    (runs for both paths — fast path may short-circuit before judge)
    # ------------------------------------------------------------------
    fp_rules = await get_fast_path_rules_for_group(agent.policy_group, tenant_id=agent.tenant_id)
    fp: FastPathResult = evaluate_fast_path(
        tool_name=body.tool_name,
        arguments=body.arguments,
        context=context_dict,
        rules=fp_rules,
    )

    latency_ms = (time.perf_counter_ns() - start_ns) / 1_000_000

    # Observe fast-path latency (before we may diverge to cognitive)
    if fp.verdict == Verdict.BLOCKED:
        _record_request_metric(agent.agent_id, body.tool_name, "blocked", "fast_path", latency_ms)

    # If fast path produced a definitive block/allow → respond immediately
    # (even for tools routed to cognitive, fast-path denylist always applies first)
    needs_judge = (
        fp.needs_cognitive
        or fp.verdict is None
        or path_decision == PathDecision.COGNITIVE
    ) and fp.verdict != Verdict.BLOCKED   # hard blocks skip judge

    # ------------------------------------------------------------------
    # 4. Cognitive path — async dispatch
    # ------------------------------------------------------------------
    if needs_judge:
        policy_version = await _active_policy_version(agent.policy_group, tenant_id=agent.tenant_id)
        await store_pending(decision_id)

        background_tasks.add_task(
            run_cognitive_evaluation,
            decision_id=decision_id,
            agent_id=agent.agent_id,
            agent_name=agent.agent_name,
            tenant_id=agent.tenant_id,
            session_id=session_id,
            tool_name=body.tool_name,
            arguments=body.arguments,
            task_description=body.context.task_description,
            conversation_history=[m.model_dump() for m in body.context.conversation_history],
            source_documents=[d.model_dump() for d in body.context.source_documents],
            policy_group=agent.policy_group,
            policy_version=policy_version,
            arguments_hash=_args_hash(body.arguments),
        )

        return PendingDecisionResponse(
            decision_id=decision_id,
            status="pending",
            poll_url=f"/v1/decisions/{decision_id}",
            estimated_ms=int(settings.judge_cognitive_path_budget_seconds * 1000),
        )

    # ------------------------------------------------------------------
    # 5. Fast path verdict — log async, respond immediately
    # ------------------------------------------------------------------
    policy_version = await _active_policy_version(agent.policy_group, tenant_id=agent.tenant_id)

    background_tasks.add_task(
        _log_audit,
        decision_id=decision_id,
        agent=agent,
        body=body,
        session_id=session_id,
        verdict=fp.verdict,
        reason=fp.reason,
        path=DecisionPath.FAST,
        rule_id=fp.rule_id,
        latency_ms=latency_ms,
        policy_version=policy_version,
    )
    background_tasks.add_task(
        _publish_decision_event,
        decision_id=decision_id,
        agent_id=agent.agent_id,
        tool_name=body.tool_name,
        verdict=fp.verdict,
        reason=fp.reason,
        path=DecisionPath.FAST,
        latency_ms=latency_ms,
        policy_version=policy_version,
    )

    verdict_str = fp.verdict.value if fp.verdict else "allowed"
    _record_request_metric(agent.agent_id, body.tool_name, verdict_str, "fast_path", latency_ms)

    return DecisionResponse(
        decision_id=decision_id,
        verdict=fp.verdict,
        reason=fp.reason,
        path=DecisionPath.FAST,
        latency_ms=round(latency_ms, 2),
        policy_version=policy_version,
        rate_limit=RateLimitInfo(
            tokens_remaining=rl.tokens_remaining,
            tokens_max=rl.tokens_max,
            reset_at=rl.reset_at,
        ),
    )


def _record_request_metric(agent_id: str, tool_name: str, verdict: str, path: str, latency_ms: float) -> None:
    requests_total.labels(agent_id=agent_id, tool_name=tool_name, verdict=verdict, path=path).inc()
    request_duration_seconds.labels(path=path).observe(latency_ms / 1000)


async def _active_policy_version(policy_group: str, tenant_id: str = "system") -> str:
    """Fetch active policy version string — used in audit records."""
    from policies.loader import get_active_policy
    policy = await get_active_policy(policy_group, tenant_id=tenant_id)
    if policy:
        return f"{policy.get('policy_group', policy_group)}-{policy.get('version', 'unknown')}"
    return "unknown"


async def _log_audit(
    decision_id: str,
    agent: AgentContext,
    body: ToolCallRequest,
    session_id: str,
    verdict: Verdict,
    reason: str,
    path: DecisionPath,
    rule_id: str,
    latency_ms: float,
    policy_version: str,
) -> None:
    """Fire-and-forget: write audit event to Redis Stream → Neo4j."""
    event = AuditEvent(
        decision_id=decision_id,
        agent_id=agent.agent_id,
        agent_name=agent.agent_name,
        tenant_id=agent.tenant_id,
        session_id=session_id,
        tool_name=body.tool_name,
        arguments_hash=_args_hash(body.arguments),
        verdict=verdict.value,
        reason=reason,
        path=path.value,
        rule_id=rule_id,
        latency_ms=latency_ms,
        policy_version=policy_version,
        timestamp=datetime.now(timezone.utc),
    )
    await enqueue_audit_event(event)


async def _publish_decision_event(
    decision_id: str,
    agent_id: str,
    tool_name: str,
    verdict: Verdict,
    reason: str,
    path: DecisionPath,
    latency_ms: float,
    policy_version: str,
) -> None:
    """Publish decision events so dashboard updates for fast path too."""
    ws_redis = websocket_client()
    try:
        event = json.dumps(
            {
                "event_type": "tool_call_decision",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "decision_id": decision_id,
                "agent_id": agent_id,
                "tool_name": tool_name,
                "verdict": verdict.value,
                "reason": reason,
                "path": path.value,
                "latency_ms": round(latency_ms, 2),
                "policy_version": policy_version,
            }
        )
        await ws_redis.publish(settings.ws_pubsub_channel, event)
    except Exception:
        pass
    finally:
        await ws_redis.aclose()
