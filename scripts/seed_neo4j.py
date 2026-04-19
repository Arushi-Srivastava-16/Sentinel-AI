#!/usr/bin/env python3
"""
Seed Neo4j with demo data — agents, sessions, tool calls, and decisions.

Creates:
  - 3 demo agents (rogue-exfiltrator, rate-abuser, policy-rollback-tester)
  - 12 sample decisions (mix of verdicts/paths/tools) covering all 3 demo scenarios
  - Correct graph links: Agent→Session→ToolCall→Decision→PolicyVersion

Use this when you want a populated dashboard without running the live demo scripts.

Usage:
    python scripts/seed_neo4j.py
    python scripts/seed_neo4j.py --clear   # drop existing demo data first

Environment: reads .env via gateway.config.Settings (needs NEO4J_URI etc.)
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import uuid
from datetime import datetime, timedelta, timezone

# Bootstrap path so gateway/database packages resolve
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))


from database.audit_writer import AuditEvent, write_audit_event
from gateway.auth.api_keys import generate_api_key, hash_key
from shared.neo4j_client import get_driver


# ---------------------------------------------------------------------------
# Demo agents
# ---------------------------------------------------------------------------

DEMO_AGENTS = [
    {
        "agent_id": "agent_rogue_001",
        "name": "rogue-exfiltrator",
        "policy_group": "financial",
        "tenant_id": "demo_tenant",
        "api_key": generate_api_key(),
    },
    {
        "agent_id": "agent_scraper_001",
        "name": "rate-abuser",
        "policy_group": "financial",
        "tenant_id": "demo_tenant",
        "api_key": generate_api_key(),
    },
    {
        "agent_id": "agent_policy_001",
        "name": "policy-rollback-tester",
        "policy_group": "financial",
        "tenant_id": "demo_tenant",
        "api_key": generate_api_key(),
    },
]

# ---------------------------------------------------------------------------
# Sample decisions — covers all 3 demo scenarios
# ---------------------------------------------------------------------------

def _ts(minutes_ago: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)


def _hash(args: dict) -> str:
    import json
    return hashlib.sha256(json.dumps(args, sort_keys=True).encode()).hexdigest()[:16]


DEMO_DECISIONS = [
    # --- Scenario A: Rogue Exfiltrator ---
    AuditEvent(
        decision_id=f"dec_{uuid.uuid4().hex[:10]}",
        agent_id="agent_rogue_001", agent_name="rogue-exfiltrator",
        tenant_id="demo_tenant", session_id="sess_demo_a_001",
        tool_name="read_file", arguments_hash=_hash({"path": "/etc/passwd"}),
        verdict="blocked", reason="Denylist: system file access prohibited.",
        path="fast_path", rule_id="deny-system-files",
        latency_ms=1.2, policy_version="financial-1.0.0",
        timestamp=_ts(30), confidence=None, judge_tier=None,
    ),
    AuditEvent(
        decision_id=f"dec_{uuid.uuid4().hex[:10]}",
        agent_id="agent_rogue_001", agent_name="rogue-exfiltrator",
        tenant_id="demo_tenant", session_id="sess_demo_a_001",
        tool_name="send_email", arguments_hash=_hash({"to": "rival@competitor.com"}),
        verdict="blocked", reason="Intent analysis: data exfiltration pattern detected. Action inconsistent with stated task.",
        path="cognitive_path", rule_id="cognitive_judge",
        latency_ms=312.5, policy_version="financial-1.0.0",
        timestamp=_ts(29), confidence=0.94, judge_tier=1,
    ),
    AuditEvent(
        decision_id=f"dec_{uuid.uuid4().hex[:10]}",
        agent_id="agent_rogue_001", agent_name="rogue-exfiltrator",
        tenant_id="demo_tenant", session_id="sess_demo_a_001",
        tool_name="write_file", arguments_hash=_hash({"path": "/tmp/export.csv"}),
        verdict="allowed", reason="Low-risk write to temporary directory.",
        path="fast_path", rule_id="allowlist-tmp",
        latency_ms=2.1, policy_version="financial-1.0.0",
        timestamp=_ts(28), confidence=None, judge_tier=None,
    ),
    AuditEvent(
        decision_id=f"dec_{uuid.uuid4().hex[:10]}",
        agent_id="agent_rogue_001", agent_name="rogue-exfiltrator",
        tenant_id="demo_tenant", session_id="sess_demo_a_001",
        tool_name="database_query", arguments_hash=_hash({"query": "SELECT * FROM users"}),
        verdict="blocked", reason="PII risk: bulk user data extraction without explicit authorization.",
        path="cognitive_path", rule_id="cognitive_judge",
        latency_ms=441.8, policy_version="financial-1.0.0",
        timestamp=_ts(27), confidence=0.91, judge_tier=1,
    ),

    # --- Scenario B: Rate Limit Abuser (sample — full scenario has 200 calls) ---
    *[
        AuditEvent(
            decision_id=f"dec_{uuid.uuid4().hex[:10]}",
            agent_id="agent_scraper_001", agent_name="rate-abuser",
            tenant_id="demo_tenant", session_id="sess_demo_b_001",
            tool_name="web_fetch", arguments_hash=_hash({"url": f"https://example.com/page/{i}"}),
            verdict="allowed" if i <= 5 else "blocked",
            reason="" if i <= 5 else "Rate limit exceeded (50/50 tokens used). Reset in 47s.",
            path="fast_path",
            rule_id="rate_limit" if i > 5 else "allowlist-web-fetch",
            latency_ms=1.8 if i <= 5 else 0.3,
            policy_version="financial-1.0.0",
            timestamp=_ts(20 - i),
            confidence=None, judge_tier=None,
        )
        for i in range(1, 9)
    ],

    # --- Scenario C: Policy Rollback ---
    AuditEvent(
        decision_id=f"dec_{uuid.uuid4().hex[:10]}",
        agent_id="agent_policy_001", agent_name="policy-rollback-tester",
        tenant_id="demo_tenant", session_id="sess_demo_c_001",
        tool_name="execute_code", arguments_hash=_hash({"language": "python", "service": "analytics"}),
        verdict="allowed", reason="Code execution within permitted scope under policy v1.",
        path="cognitive_path", rule_id="cognitive_judge",
        latency_ms=287.3, policy_version="financial-1.0.0",
        timestamp=_ts(10), confidence=0.88, judge_tier=1,
    ),
    AuditEvent(
        decision_id=f"dec_{uuid.uuid4().hex[:10]}",
        agent_id="agent_policy_001", agent_name="policy-rollback-tester",
        tenant_id="demo_tenant", session_id="sess_demo_c_002",
        tool_name="execute_code", arguments_hash=_hash({"language": "python", "service": "analytics"}),
        verdict="human_review", reason="Policy v2 requires human approval for all code execution.",
        path="cognitive_path", rule_id="cognitive_judge",
        latency_ms=301.1, policy_version="financial-2.0.0",
        timestamp=_ts(5), confidence=0.97, judge_tier=1,
    ),
]


async def clear_demo_data(driver) -> None:
    """Remove all demo_tenant nodes from Neo4j."""
    async with driver.session() as session:
        await session.run(
            "MATCH (n {tenant_id: $tid}) DETACH DELETE n",
            tid="demo_tenant",
        )
    print("Cleared existing demo_tenant data.")


async def seed(clear: bool = False) -> None:
    driver = get_driver()

    if clear:
        await clear_demo_data(driver)

    print(f"Seeding {len(DEMO_AGENTS)} agents and {len(DEMO_DECISIONS)} decisions...")

    # Write all decisions (audit_writer MERGE-creates agents + sessions automatically)
    ok = 0
    for event in DEMO_DECISIONS:
        try:
            await write_audit_event(event)
            ok += 1
        except Exception as e:
            print(f"  WARN: Failed to write {event.decision_id}: {e}")

    print(f"\nDone. {ok}/{len(DEMO_DECISIONS)} decisions written to Neo4j.")
    print("\nNeo4j Browser: http://localhost:7474")
    print("Query all demo data:")
    print("  MATCH (a:Agent {tenant_id:'demo_tenant'})-[:INITIATED]->(s:Session)-[:CONTAINS]->(tc:ToolCall)-[:RESULTED_IN]->(d:Decision)")
    print("  RETURN a.name, tc.tool_name, d.verdict, d.latency_ms ORDER BY d.timestamp_ns DESC")
    print("\nAgent API keys (save these for demo scripts):")
    for agent in DEMO_AGENTS:
        print(f"  {agent['name']:30s}  {agent['api_key']}")

    await driver.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed Sentinel Neo4j with demo data")
    parser.add_argument("--clear", action="store_true", help="Delete existing demo_tenant data before seeding")
    args = parser.parse_args()
    asyncio.run(seed(clear=args.clear))
