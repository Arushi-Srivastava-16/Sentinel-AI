"""
GET /v1/decisions/{decision_id} — poll for async cognitive path results.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from gateway.cognitive_path.handler import get_decision_result
from gateway.middleware.auth import AgentContext, require_agent
from gateway.models.requests import (
    DecisionPath,
    DecisionResponse,
    PendingDecisionResponse,
    Verdict,
)

router = APIRouter(prefix="/v1", tags=["Tool Calls"])


@router.get(
    "/decisions/{decision_id}",
    response_model=DecisionResponse | PendingDecisionResponse,
)
async def poll_decision(
    decision_id: str,
    agent: AgentContext = Depends(require_agent),
) -> DecisionResponse | PendingDecisionResponse:
    """Poll for a pending async decision."""
    result = await get_decision_result(decision_id)

    if result is None:
        # Still pending
        return PendingDecisionResponse(
            decision_id=decision_id,
            status="pending",
            poll_url=f"/v1/decisions/{decision_id}",
            estimated_ms=2000,
        )

    if result == {}:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "not_found", "message": f"Decision '{decision_id}' not found."},
        )

    return DecisionResponse(
        decision_id=result["decision_id"],
        verdict=Verdict(result["verdict"]),
        reason=result.get("reason", ""),
        path=DecisionPath(result.get("path", "cognitive_path")),
        latency_ms=result.get("latency_ms", 0.0),
        policy_version=result.get("policy_version", "unknown"),
        confidence=result.get("confidence"),
    )
