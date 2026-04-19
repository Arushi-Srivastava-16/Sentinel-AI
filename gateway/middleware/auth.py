"""
FastAPI dependency for API key authentication.

Usage:
    from gateway.middleware.auth import require_agent, require_admin

    @router.post("/v1/tool-calls")
    async def evaluate(req: ToolCallRequest, agent: AgentContext = Depends(require_agent)):
        ...
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer

from gateway.auth.api_keys import validate_api_key
from shared.redis_client import rate_limit_client

_API_KEY_HEADER = APIKeyHeader(name="X-Sentinel-Agent-Key", auto_error=False)
_BEARER_HEADER = HTTPBearer(auto_error=False)


@dataclass
class AgentContext:
    """Parsed, validated agent identity attached to each request."""
    agent_id: str
    agent_name: str
    policy_group: str
    tenant_id: str
    is_admin: bool
    raw_key: str


async def _resolve_agent(
    request: Request,
    api_key: str | None = Depends(_API_KEY_HEADER),
    bearer: HTTPAuthorizationCredentials | None = Depends(_BEARER_HEADER),
) -> AgentContext:
    """
    Resolve agent identity from either:
      1. X-Sentinel-Agent-Key header (API key — used by SDK, admin CLI, demos)
      2. Authorization: Bearer <jwt> header (short-lived JWT from /v1/auth/token)
    """
    # --- Path 1: JWT Bearer token ---
    if bearer and bearer.credentials:
        return await _resolve_from_jwt(bearer.credentials)

    # --- Path 2: API key header ---
    if api_key:
        return await _resolve_from_api_key(api_key)

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={
            "error": "missing_credentials",
            "message": "Provide X-Sentinel-Agent-Key header or Authorization: Bearer <jwt>.",
        },
    )


async def _resolve_from_api_key(api_key: str) -> AgentContext:
    redis = rate_limit_client()
    metadata = await validate_api_key(redis, api_key)
    await redis.aclose()

    if metadata is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "invalid_api_key", "message": "API key is invalid or revoked."},
        )
    return AgentContext(
        agent_id=metadata["agent_id"],
        agent_name=metadata["agent_name"],
        policy_group=metadata["policy_group"],
        tenant_id=metadata["tenant_id"],
        is_admin=metadata.get("is_admin", False),
        raw_key=api_key,
    )


async def _resolve_from_jwt(token: str) -> AgentContext:
    from gateway.auth.jwt import JWTAuthError, verify_jwt

    try:
        claims = verify_jwt(token)
    except JWTAuthError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "invalid_jwt", "message": str(e)},
        )

    # JWTs are never admin — admins always use API keys
    return AgentContext(
        agent_id=claims["sub"],
        agent_name=claims.get("sub", "unknown"),   # JWT doesn't carry display name
        policy_group=claims.get("policy_group", "default"),
        tenant_id=claims.get("tenant_id", "system"),
        is_admin=False,
        raw_key=token,
    )


async def require_agent(agent: AgentContext = Depends(_resolve_agent)) -> AgentContext:
    """Require any valid (non-admin) agent API key."""
    return agent


async def require_admin(agent: AgentContext = Depends(_resolve_agent)) -> AgentContext:
    """Require the root admin key (for agent registration, policy activation)."""
    if not agent.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "forbidden", "message": "Admin key required for this endpoint."},
        )
    return agent
