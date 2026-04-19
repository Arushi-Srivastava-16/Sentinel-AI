"""
Agent registry Pydantic models.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class AgentRegistrationRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=64)
    policy_group: str = Field(
        ...,
        pattern="^(financial|data-access|code-execution|communication|infrastructure|default)$",
    )
    tenant_id: str = "default"
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentRegistrationResponse(BaseModel):
    agent_id: str
    api_key: str = Field(..., description="Store this securely — shown only once.")
    name: str
    policy_group: str
    tenant_id: str
    registered_at: datetime


class AgentInfo(BaseModel):
    agent_id: str
    name: str
    policy_group: str
    tenant_id: str
    registered_at: datetime
    total_requests: int = 0
    last_active: datetime | None = None


class TokenRequest(BaseModel):
    agent_id: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    expires_in: int = 900  # 15 minutes
