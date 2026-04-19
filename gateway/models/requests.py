"""
Request and response Pydantic models for the gateway API.
These mirror gateway/openapi.yaml exactly.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


# =============================================================================
# Enums
# =============================================================================

class Verdict(str, Enum):
    ALLOWED = "ALLOWED"
    BLOCKED = "BLOCKED"
    HUMAN_REVIEW = "HUMAN_REVIEW"


class DecisionPath(str, Enum):
    FAST = "fast_path"
    COGNITIVE = "cognitive_path"


# =============================================================================
# Tool Call — inbound request
# =============================================================================

class ConversationMessage(BaseModel):
    role: str = Field(..., pattern="^(user|assistant|system)$")
    content: str = Field(..., max_length=2000)


class SourceDocument(BaseModel):
    name: str
    excerpt: str = Field(default="", max_length=2000)


class RequestContext(BaseModel):
    task_description: str = Field(default="", max_length=1000)
    conversation_history: list[ConversationMessage] = Field(default_factory=list, max_length=20)
    source_documents: list[SourceDocument] = Field(default_factory=list, max_length=5)


class RequestMetadata(BaseModel):
    agent_version: str = ""
    request_id: str = Field(default_factory=lambda: str(uuid4()))
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class ToolCallRequest(BaseModel):
    tool_name: str = Field(..., min_length=1, max_length=128)
    arguments: dict[str, Any] = Field(default_factory=dict)
    session_id: str | None = None
    context: RequestContext = Field(default_factory=RequestContext)
    metadata: RequestMetadata = Field(default_factory=RequestMetadata)


# =============================================================================
# Decision — outbound response
# =============================================================================

class RateLimitInfo(BaseModel):
    tokens_remaining: int
    tokens_max: int
    reset_at: datetime


class DecisionResponse(BaseModel):
    decision_id: str
    verdict: Verdict
    reason: str = ""
    path: DecisionPath
    latency_ms: float
    policy_version: str
    confidence: float | None = None
    rate_limit: RateLimitInfo | None = None


class PendingDecisionResponse(BaseModel):
    decision_id: str
    status: str = "pending"
    poll_url: str
    estimated_ms: int = 5000


# =============================================================================
# Health
# =============================================================================

class ServiceStatus(str, Enum):
    OK = "ok"
    DOWN = "down"
    CIRCUIT_OPEN = "circuit_open"


class ServicesHealth(BaseModel):
    redis: ServiceStatus = ServiceStatus.OK
    neo4j: ServiceStatus = ServiceStatus.OK
    ollama: ServiceStatus = ServiceStatus.OK


class HealthResponse(BaseModel):
    status: str  # "ok" | "degraded"
    version: str = "1.0.0"
    services: ServicesHealth = Field(default_factory=ServicesHealth)


# =============================================================================
# Errors
# =============================================================================

class ErrorResponse(BaseModel):
    error: str
    message: str
    details: dict[str, Any] | None = None
