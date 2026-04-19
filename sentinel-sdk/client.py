"""
Sentinel SDK — real AgentClient implementation.
Replaces the stub in __init__.py with a working httpx-based client.

Usage:
    import asyncio
    from sentinel_sdk.client import AgentClient

    async def main():
        async with AgentClient(
            gateway_url="http://localhost:8000",
            api_key="snl_...",
            agent_id="agent_abc",
        ) as sentinel:
            decision = await sentinel.check(
                tool_name="execute_payment",
                arguments={"amount": 75000, "currency": "USD", "recipient": "vendor_abc"},
                context={"task_description": "Pay Q1 invoice per clause 4.2"},
            )
            if decision.is_allowed:
                print("Payment approved!")
            else:
                print(f"Payment blocked: {decision.reason}")

    asyncio.run(main())
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

import httpx

from sentinel_sdk import (
    AgentClient as _AgentClientBase,
    BlockedBySentinel,
    Decision,
    DecisionPath,
    SentinelAuthError,
    SentinelConnectionError,
    Verdict,
)

_POLL_INTERVAL_MS = 500
_MAX_POLL_ATTEMPTS = 40   # 40 × 500ms = 20s max wait


class AgentClient(_AgentClientBase):
    """
    Full implementation of the Sentinel AgentClient.
    Handles both sync (200) and async (202 + polling) decisions.
    """

    def __init__(
        self,
        gateway_url: str,
        api_key: str,
        agent_id: str,
        session_id: str | None = None,
        timeout_seconds: float = 25.0,
    ) -> None:
        super().__init__(
            gateway_url=gateway_url,
            api_key=api_key,
            agent_id=agent_id,
            session_id=session_id,
            timeout_seconds=timeout_seconds,
        )
        self._http_client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "AgentClient":
        self._http_client = httpx.AsyncClient(
            base_url=self.gateway_url,
            headers={
                "X-Sentinel-Agent-Key": self.api_key,
                "Content-Type": "application/json",
            },
            timeout=self.timeout_seconds,
        )
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None

    async def check(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> Decision:
        if self._http_client is None:
            raise RuntimeError("AgentClient must be used as an async context manager.")

        payload: dict[str, Any] = {
            "tool_name":   tool_name,
            "arguments":   arguments,
            "session_id":  self.session_id,
            "metadata":    {"request_id": str(uuid.uuid4())},
        }
        if context:
            payload["context"] = {
                "task_description":    context.get("task_description", ""),
                "conversation_history": context.get("conversation_history", []),
                "source_documents":    context.get("source_documents", []),
            }

        try:
            resp = await self._http_client.post("/v1/tool-calls", json=payload)
        except httpx.ConnectError as e:
            raise SentinelConnectionError(f"Cannot reach Sentinel gateway: {e}") from e
        except httpx.TimeoutException as e:
            raise SentinelConnectionError(f"Sentinel gateway timed out: {e}") from e

        if resp.status_code == 401:
            raise SentinelAuthError("Invalid or missing API key.")

        if resp.status_code == 429:
            data = resp.json()
            return Decision(
                decision_id=f"rate_limit_{uuid.uuid4().hex[:6]}",
                verdict=Verdict.BLOCKED,
                reason=data.get("detail", {}).get("message", "Rate limit exceeded."),
                path=DecisionPath.FAST,
                latency_ms=0.0,
                policy_version="n/a",
            )

        resp.raise_for_status()
        data = resp.json()

        # Synchronous decision (200)
        if resp.status_code == 200 and "verdict" in data:
            return _parse_decision(data)

        # Async pending (202) — poll until resolved
        if resp.status_code == 202 or data.get("status") == "pending":
            return await self._poll_decision(data["decision_id"], data.get("poll_url"))

        return _parse_decision(data)

    async def _poll_decision(self, decision_id: str, poll_url: str | None) -> Decision:
        url = poll_url or f"/v1/decisions/{decision_id}"
        for attempt in range(_MAX_POLL_ATTEMPTS):
            await asyncio.sleep(_POLL_INTERVAL_MS / 1000)
            try:
                resp = await self._http_client.get(url)   # type: ignore[union-attr]
                resp.raise_for_status()
                data = resp.json()
                if data.get("status") == "pending":
                    continue
                return _parse_decision(data)
            except httpx.HTTPStatusError:
                continue

        # Exhausted polls — return safe fallback
        return Decision(
            decision_id=decision_id,
            verdict=Verdict.HUMAN_REVIEW,
            reason="Decision timed out after max poll attempts.",
            path=DecisionPath.COGNITIVE,
            latency_ms=_MAX_POLL_ATTEMPTS * _POLL_INTERVAL_MS,
            policy_version="unknown",
        )


def _parse_decision(data: dict[str, Any]) -> Decision:
    return Decision(
        decision_id=data["decision_id"],
        verdict=Verdict(data["verdict"]),
        reason=data.get("reason", ""),
        path=DecisionPath(data.get("path", "fast_path")),
        latency_ms=float(data.get("latency_ms", 0)),
        policy_version=data.get("policy_version", "unknown"),
        confidence=data.get("confidence"),
        rate_limit_remaining=(
            data["rate_limit"]["tokens_remaining"]
            if data.get("rate_limit")
            else None
        ),
    )
