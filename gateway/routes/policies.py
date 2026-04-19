"""
Policy management routes.

GET  /v1/policies                         — list all policy versions
POST /v1/policies/{group}/activate        — activate a version (admin only)
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from gateway.middleware.auth import AgentContext, require_admin, require_agent
from policies.loader import activate_policy_version, list_available_policies

router = APIRouter(prefix="/v1", tags=["Policies"])


class PolicyVersionInfo(BaseModel):
    id: str
    policy_group: str
    version: str
    description: str = ""
    effective_from: str
    effective_until: str | None = None
    parent_version: str | None = None


class ActivateRequest(BaseModel):
    version: str


@router.get("/policies", response_model=list[PolicyVersionInfo])
async def list_policies(
    caller: AgentContext = Depends(require_agent),
) -> list[PolicyVersionInfo]:
    groups = ["financial", "data-access", "code-execution", "communication", "infrastructure", "default"]
    result = []
    for group in groups:
        for p in list_available_policies(group):
            result.append(PolicyVersionInfo(
                id=p.get("id", ""),
                policy_group=p.get("policy_group", group),
                version=p.get("version", ""),
                description=p.get("description", ""),
                effective_from=str(p.get("effective_from", "")),
                effective_until=str(p["effective_until"]) if p.get("effective_until") else None,
                parent_version=p.get("parent_version"),
            ))
    return result


@router.post("/policies/{policy_group}/activate", response_model=PolicyVersionInfo)
async def activate_policy(
    policy_group: str,
    body: ActivateRequest,
    admin: AgentContext = Depends(require_admin),
) -> PolicyVersionInfo:
    policy = await activate_policy_version(policy_group, body.version, tenant_id=admin.tenant_id)
    if not policy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "policy_version_not_found",
                "message": f"Version '{body.version}' not found for policy_group '{policy_group}'.",
            },
        )
    return PolicyVersionInfo(
        id=policy.get("id", ""),
        policy_group=policy.get("policy_group", policy_group),
        version=policy.get("version", ""),
        description=policy.get("description", ""),
        effective_from=str(policy.get("effective_from", "")),
        effective_until=str(policy["effective_until"]) if policy.get("effective_until") else None,
        parent_version=policy.get("parent_version"),
    )
