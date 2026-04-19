"""
Synchronous Neo4j audit writer.
Called by the stream consumer worker (not the gateway directly).

The gateway writes to Redis Stream first (fast, non-blocking).
This module consumes from the stream and persists to Neo4j.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from shared.neo4j_client import get_driver
from gateway.config import settings


@dataclass
class AuditEvent:
    decision_id: str
    agent_id: str
    agent_name: str
    tenant_id: str
    session_id: str
    tool_name: str
    arguments_hash: str
    verdict: str              # "ALLOWED" | "BLOCKED" | "HUMAN_REVIEW"
    reason: str
    path: str                 # "fast_path" | "cognitive_path"
    rule_id: str
    latency_ms: float
    policy_version: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    confidence: float | None = None
    judge_tier: int | None = None


_WRITE_CYPHER = """
MERGE (agent:Agent {id: $agent_id})
  ON CREATE SET
    agent.name       = $agent_name,
    agent.tenant_id  = $tenant_id,
    agent.created_at = datetime()

MERGE (session:Session {id: $session_id})
  ON CREATE SET
    session.agent_id   = $agent_id,
    session.tenant_id  = $tenant_id,
    session.started_at = datetime($timestamp)

// Link agent → session
MERGE (agent)-[:INITIATED]->(session)

// Create tool call node (always new — decisions are immutable)
CREATE (tc:ToolCall {
  id:               $tool_call_id,
  agent_id:         $agent_id,
  tenant_id:        $tenant_id,
  tool_name:        $tool_name,
  arguments_hash:   $arguments_hash,
  session_id:       $session_id,
  timestamp_ns:     $timestamp_ns
})

// Link session → tool call
MERGE (session)-[:CONTAINS]->(tc)

// Create decision node
CREATE (dec:Decision {
  id:               $decision_id,
  verdict:          $verdict,
  reason:           $reason,
  path:             $path,
  rule_id:          $rule_id,
  latency_ms:       $latency_ms,
  policy_version:   $policy_version,
  timestamp_ns:     $timestamp_ns,
  confidence:       $confidence
})

// Link tool call → decision
MERGE (tc)-[:RESULTED_IN]->(dec)

// Link decision → policy version (create PolicyVersion node if first time seen)
MERGE (pv:PolicyVersion {id: $policy_version})
  ON CREATE SET
    pv.version      = $policy_version,
    pv.policy_group = $policy_group,
    pv.created_at   = datetime()
MERGE (dec)-[:EVALUATED_UNDER]->(pv)

RETURN dec.id AS written
"""


async def write_audit_event(event: AuditEvent) -> bool:
    """
    Write an audit event to Neo4j.
    Returns True on success, False on failure (caller decides retry logic).
    """
    import uuid as _uuid
    driver = get_driver()
    try:
        async with driver.session(database=settings.neo4j_database) as session:
            timestamp_ns = int(event.timestamp.timestamp() * 1_000_000_000)
            policy_group = event.policy_version.rsplit("-", 1)[0] if "-" in event.policy_version else event.policy_version
            result = await session.run(
                _WRITE_CYPHER,
                agent_id=event.agent_id,
                agent_name=event.agent_name,
                tenant_id=event.tenant_id,
                session_id=event.session_id,
                tool_call_id=f"tc_{_uuid.uuid4().hex[:10]}",
                tool_name=event.tool_name,
                arguments_hash=event.arguments_hash,
                decision_id=event.decision_id,
                verdict=event.verdict,
                reason=event.reason,
                path=event.path,
                rule_id=event.rule_id or "",
                latency_ms=event.latency_ms,
                policy_version=event.policy_version,
                policy_group=policy_group,
                timestamp=event.timestamp.isoformat(),
                timestamp_ns=timestamp_ns,
                confidence=event.confidence,
            )
            record = await result.single()
            return record is not None
    except Exception as exc:
        # Log but don't crash — audit failures must not block governance
        import structlog
        log = structlog.get_logger()
        log.error("neo4j_write_failed", decision_id=event.decision_id, error=str(exc))
        return False
