"""
Sentinel SDK — thin Python client for agent integration.

Agents wrap their tool calls with this SDK so Sentinel's gateway is
consulted before any real execution.

Usage:
    from sentinel_sdk import AgentClient, ToolCall

    sentinel = AgentClient(
        gateway_url="http://localhost:8000",
        api_key="snl_...",
        agent_id="agent_01hx...",
    )

    # Before executing a tool, ask Sentinel
    result = await sentinel.check(
        tool_name="execute_payment",
        arguments={"amount": 75000, "currency": "USD", "recipient": "vendor_abc"},
        context={"task_description": "Pay Q1 invoice per contract clause 4.2"},
    )

    if result.is_allowed:
        # execute the real tool
        ...
    else:
        raise RuntimeError(f"Sentinel blocked: {result.reason}")
"""

from __future__ import annotations

import asyncio
import hashlib
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# ---------------------------------------------------------------------------
# Enums / Value objects
# ---------------------------------------------------------------------------

class Verdict(str, Enum):
    ALLOWED = "ALLOWED"
    BLOCKED = "BLOCKED"
    HUMAN_REVIEW = "HUMAN_REVIEW"


class DecisionPath(str, Enum):
    FAST = "fast_path"
    COGNITIVE = "cognitive_path"


@dataclass
class ToolCall:
    tool_name: str
    arguments: dict[str, Any]
    session_id: str | None = None
    context: dict[str, Any] = field(default_factory=dict)
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass
class Decision:
    decision_id: str
    verdict: Verdict
    reason: str
    path: DecisionPath
    latency_ms: float
    policy_version: str
    confidence: float | None = None
    rate_limit_remaining: int | None = None

    @property
    def is_allowed(self) -> bool:
        return self.verdict == Verdict.ALLOWED

    @property
    def is_blocked(self) -> bool:
        return self.verdict == Verdict.BLOCKED

    @property
    def needs_human(self) -> bool:
        return self.verdict == Verdict.HUMAN_REVIEW


# ---------------------------------------------------------------------------
# AgentClient
# ---------------------------------------------------------------------------

class AgentClient:
    """
    Async client for the Sentinel Gateway.

    This is intentionally a stub — the full implementation lives in
    sentinel-sdk/client.py (Phase 1). This file defines the interface
    contract that demo agents depend on.
    """

    def __init__(
        self,
        gateway_url: str,
        api_key: str,
        agent_id: str,
        session_id: str | None = None,
        timeout_seconds: float = 25.0,
    ) -> None:
        self.gateway_url = gateway_url.rstrip("/")
        self.api_key = api_key
        self.agent_id = agent_id
        self.session_id = session_id or f"sess_{uuid.uuid4().hex[:8]}"
        self.timeout_seconds = timeout_seconds
        self._http_client: Any = None   # httpx.AsyncClient — injected in Phase 1

    async def __aenter__(self) -> "AgentClient":
        # Phase 1: initialise httpx.AsyncClient here
        return self

    async def __aexit__(self, *_: Any) -> None:
        # Phase 1: close httpx.AsyncClient here
        pass

    async def check(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> Decision:
        """
        Ask Sentinel whether this tool call is allowed.

        Raises:
            SentinelConnectionError: if gateway is unreachable
            SentinelAuthError: if API key is invalid
        """
        # STUB — Phase 1 will implement the actual HTTP call
        raise NotImplementedError(
            "AgentClient.check() is a stub. Implement in Phase 1."
        )

    async def check_and_raise(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> Decision:
        """
        Like check(), but raises BlockedBysentinel if verdict != ALLOWED.
        Convenience wrapper for agents that want fail-fast behaviour.
        """
        decision = await self.check(tool_name, arguments, context)
        if not decision.is_allowed:
            raise BlockedBySentinel(decision)
        return decision


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class SentinelError(Exception):
    """Base class for all Sentinel SDK errors."""


class BlockedBySentinel(SentinelError):
    """Raised by check_and_raise() when verdict is BLOCKED or HUMAN_REVIEW."""

    def __init__(self, decision: Decision) -> None:
        self.decision = decision
        super().__init__(
            f"Sentinel {decision.verdict.value}: {decision.reason} "
            f"(decision_id={decision.decision_id})"
        )


class SentinelConnectionError(SentinelError):
    """Gateway is unreachable."""


class SentinelAuthError(SentinelError):
    """API key or JWT is invalid."""


# ---------------------------------------------------------------------------
# Convenience: build a mock decision (for unit tests / demo stubs)
# ---------------------------------------------------------------------------

def mock_decision(
    verdict: Verdict = Verdict.ALLOWED,
    reason: str = "mock",
    path: DecisionPath = DecisionPath.FAST,
    latency_ms: float = 5.0,
    policy_version: str = "financial-v1.0.0",
) -> Decision:
    return Decision(
        decision_id=f"dec_{uuid.uuid4().hex[:10]}",
        verdict=verdict,
        reason=reason,
        path=path,
        latency_ms=latency_ms,
        policy_version=policy_version,
    )


# ---------------------------------------------------------------------------
# Wire in the real HTTP implementation from client.py when httpx is available.
# client.py subclasses the stub AgentClient above and overrides check() with
# a real httpx-based implementation.  By replacing AgentClient here at module
# load time, all demo scripts that do `from sentinel_sdk import AgentClient`
# automatically get the live client without any import changes.
# ---------------------------------------------------------------------------
try:
    from sentinel_sdk import client as _impl_module  # noqa: E402
    AgentClient = _impl_module.AgentClient           # noqa: F811
except Exception:
    pass  # fall back to stub (unit tests / environments without httpx)
