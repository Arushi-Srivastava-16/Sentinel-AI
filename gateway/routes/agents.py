"""
Agent registry routes.

POST /v1/agents          — register agent (admin only)
GET  /v1/agents/{id}     — get agent info
POST /v1/auth/token      — exchange API key for JWT (stub — Phase 2)
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status

from gateway.auth.api_keys import generate_api_key, store_api_key
from gateway.middleware.auth import AgentContext, require_admin, require_agent
from gateway.models.agents import (
    AgentInfo,
    AgentRegistrationRequest,
    AgentRegistrationResponse,
    TokenRequest,
    TokenResponse,
)
from shared.redis_client import rate_limit_client

router = APIRouter(prefix="/v1", tags=["Agents"])


@router.post(
    "/agents",
    response_model=AgentRegistrationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register_agent(
    body: AgentRegistrationRequest,
    admin: AgentContext = Depends(require_admin),
) -> AgentRegistrationResponse:
    """Register a new agent and issue an API key (admin key required)."""
    agent_id = f"agent_{uuid.uuid4().hex[:8]}"
    api_key = generate_api_key()

    redis = rate_limit_client()
    try:
        # Check for duplicate name within tenant
        existing_key = f"agent:name:{body.tenant_id}:{body.name}"
        if await redis.get(existing_key):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "agent_exists",
                    "message": f"Agent '{body.name}' already registered in tenant '{body.tenant_id}'.",
                },
            )

        await store_api_key(
            redis=redis,
            raw_key=api_key,
            agent_id=agent_id,
            agent_name=body.name,
            policy_group=body.policy_group,
            tenant_id=body.tenant_id,
        )

        # Store name → agent_id reverse index to detect duplicates
        await redis.set(existing_key, agent_id)

        # Store full agent info for GET /agents/{id}
        import json
        registered_at = datetime.utcnow()
        await redis.set(
            f"agent:info:{agent_id}",
            json.dumps({
                "agent_id": agent_id,
                "name": body.name,
                "policy_group": body.policy_group,
                "tenant_id": body.tenant_id,
                "registered_at": registered_at.isoformat(),
                "total_requests": 0,
                "last_active": None,
            }),
        )
    finally:
        await redis.aclose()

    return AgentRegistrationResponse(
        agent_id=agent_id,
        api_key=api_key,
        name=body.name,
        policy_group=body.policy_group,
        tenant_id=body.tenant_id,
        registered_at=registered_at,
    )


@router.get("/agents/{agent_id}", response_model=AgentInfo)
async def get_agent(
    agent_id: str,
    caller: AgentContext = Depends(require_agent),
) -> AgentInfo:
    """Get agent info. Agents can only see their own info unless admin."""
    import json

    if not caller.is_admin and caller.agent_id != agent_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "forbidden", "message": "You can only view your own agent info."},
        )

    redis = rate_limit_client()
    try:
        raw = await redis.get(f"agent:info:{agent_id}")
    finally:
        await redis.aclose()

    if not raw:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "not_found", "message": f"Agent '{agent_id}' not found."},
        )

    data = json.loads(raw)
    return AgentInfo(
        agent_id=data["agent_id"],
        name=data["name"],
        policy_group=data["policy_group"],
        tenant_id=data["tenant_id"],
        registered_at=datetime.fromisoformat(data["registered_at"]),
        total_requests=data.get("total_requests", 0),
        last_active=datetime.fromisoformat(data["last_active"]) if data.get("last_active") else None,
    )


@router.post("/auth/token", response_model=TokenResponse)
async def issue_jwt(
    body: TokenRequest,
    agent: AgentContext = Depends(require_agent),
) -> TokenResponse:
    """
    Exchange a long-lived API key for a short-lived RS256 JWT (TTL: 15 minutes).
    The JWT can be used as a Bearer token in subsequent requests.
    """
    from gateway.auth.jwt import sign_jwt
    import uuid as _uuid

    session_id = body.session_id or f"sess_{_uuid.uuid4().hex[:8]}"
    token = sign_jwt(
        agent_id=agent.agent_id,
        tenant_id=agent.tenant_id,
        policy_group=agent.policy_group,
        session_id=session_id,
    )
    from gateway.config import settings
    return TokenResponse(
        access_token=token,
        expires_in=settings.jwt_ttl_minutes * 60,
        token_type="bearer",
    )
