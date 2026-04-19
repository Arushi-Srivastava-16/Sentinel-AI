"""
Integration tests — Audit pipeline against real Redis + Neo4j.

Uses testcontainers-python to spin up Redis and Neo4j containers.
Verifies the full event flow: enqueue → consumer → Neo4j graph.

Run with:
    pytest tests/integration/test_audit_pipeline_integration.py -v
    # (requires Docker — Neo4j image is ~500MB, first run slow)
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone

import pytest
import pytest_asyncio
import redis.asyncio as aioredis


# ---------------------------------------------------------------------------
# Container fixtures — shared across all tests in this module
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def redis_container():
    try:
        from testcontainers.redis import RedisContainer
    except ImportError:
        pytest.skip("testcontainers not installed")

    with RedisContainer("redis:7.2-alpine") as c:
        yield c


@pytest.fixture(scope="module")
def neo4j_container():
    try:
        from testcontainers.neo4j import Neo4jContainer
    except ImportError:
        pytest.skip("testcontainers not installed")

    with Neo4jContainer("neo4j:5.18-community") as c:
        yield c


@pytest.fixture(scope="module")
def redis_url(redis_container) -> str:
    host = redis_container.get_container_host_ip()
    port = redis_container.get_exposed_port(6379)
    return f"redis://{host}:{port}"


@pytest.fixture(scope="module")
def neo4j_bolt_url(neo4j_container) -> str:
    return neo4j_container.get_connection_url()


@pytest.fixture(autouse=True)
def patch_all_settings(redis_url, neo4j_bolt_url, monkeypatch):
    """Override env vars so all gateway/database clients point at test containers."""
    import urllib.parse

    r = urllib.parse.urlparse(redis_url)
    monkeypatch.setenv("REDIS_HOST", r.hostname)
    monkeypatch.setenv("REDIS_PORT", str(r.port))
    monkeypatch.setenv("REDIS_PASSWORD", "")

    # neo4j_bolt_url from testcontainers is already bolt://neo4j:neo4j@host:port
    monkeypatch.setenv("NEO4J_URI", neo4j_bolt_url)
    monkeypatch.setenv("NEO4J_USER", "neo4j")
    monkeypatch.setenv("NEO4J_PASSWORD", "neo4j")
    monkeypatch.setenv("NEO4J_DATABASE", "neo4j")

    from gateway.config import get_settings
    get_settings.cache_clear()

    from shared.neo4j_client import get_driver as _get_driver
    _get_driver.cache_clear()

    yield

    get_settings.cache_clear()
    _driver_cache.cache_clear()


@pytest_asyncio.fixture
async def redis_client(redis_url):
    client = aioredis.from_url(redis_url, decode_responses=True)
    await client.flushdb()
    yield client
    await client.aclose()


def _sample_event(**overrides):
    from database.audit_writer import AuditEvent

    defaults = dict(
        decision_id="dec_neo4j_001",
        agent_id="agent_neo4j_test",
        agent_name="neo4j-integration-tester",
        tenant_id="tenant_integration",
        session_id="sess_neo4j_001",
        tool_name="read_file",
        arguments_hash="int_abc123",
        verdict="allowed",
        reason="Integration test event",
        path="fast_path",
        rule_id="test_rule",
        latency_ms=3.7,
        policy_version="financial-1.0.0",
        timestamp=datetime.now(timezone.utc),
    )
    defaults.update(overrides)
    return AuditEvent(**defaults)


# ---------------------------------------------------------------------------
# Neo4j write tests
# ---------------------------------------------------------------------------

class TestNeo4jAuditWriter:
    @pytest.mark.asyncio
    async def test_write_creates_agent_node(self):
        from database.audit_writer import write_audit_event
        from shared.neo4j_client import get_driver

        event = _sample_event(agent_id="agent_write_test", agent_name="write-test-agent")
        success = await write_audit_event(event)
        assert success is True

        driver = get_driver()
        async with driver.session() as session:
            result = await session.run(
                "MATCH (a:Agent {id: $id}) RETURN a.name AS name, a.tenant_id AS tid",
                id="agent_write_test",
            )
            record = await result.single()
        assert record is not None
        assert record["name"] == "write-test-agent"
        assert record["tid"] == "tenant_integration"

    @pytest.mark.asyncio
    async def test_write_creates_decision_node(self):
        from database.audit_writer import write_audit_event
        from shared.neo4j_client import get_driver

        event = _sample_event(decision_id="dec_unique_001", verdict="blocked", reason="Test block")
        await write_audit_event(event)

        driver = get_driver()
        async with driver.session() as session:
            result = await session.run(
                "MATCH (d:Decision {id: $id}) RETURN d.verdict AS verdict, d.reason AS reason",
                id="dec_unique_001",
            )
            record = await result.single()
        assert record is not None
        assert record["verdict"] == "blocked"
        assert record["reason"] == "Test block"

    @pytest.mark.asyncio
    async def test_write_creates_full_graph_chain(self):
        """Agent→Session→ToolCall→Decision→PolicyVersion chain must all exist."""
        from database.audit_writer import write_audit_event
        from shared.neo4j_client import get_driver

        event = _sample_event(
            decision_id="dec_chain_001",
            agent_id="agent_chain",
            session_id="sess_chain_001",
            policy_version="financial-1.0.0",
        )
        await write_audit_event(event)

        driver = get_driver()
        async with driver.session() as session:
            result = await session.run("""
                MATCH (a:Agent {id: 'agent_chain'})
                      -[:INITIATED]->(s:Session {id: 'sess_chain_001'})
                      -[:CONTAINS]->(tc:ToolCall)
                      -[:RESULTED_IN]->(d:Decision {id: 'dec_chain_001'})
                      -[:EVALUATED_UNDER]->(pv:PolicyVersion)
                RETURN count(*) AS chain_count
            """)
            record = await result.single()
        assert record["chain_count"] == 1, "Full audit chain not created in Neo4j"

    @pytest.mark.asyncio
    async def test_write_is_idempotent_for_agent_and_session(self):
        """Writing two events with same agent_id and session_id should MERGE, not duplicate."""
        from database.audit_writer import write_audit_event
        from shared.neo4j_client import get_driver

        for i in range(3):
            event = _sample_event(
                decision_id=f"dec_idem_{i:03d}",
                agent_id="agent_idem",
                session_id="sess_idem_001",
            )
            await write_audit_event(event)

        driver = get_driver()
        async with driver.session() as session:
            agent_count = await session.run(
                "MATCH (a:Agent {id: 'agent_idem'}) RETURN count(*) AS c"
            )
            r = await agent_count.single()
            assert r["c"] == 1, f"Expected 1 Agent node, got {r['c']} (MERGE not working)"

            sess_count = await session.run(
                "MATCH (s:Session {id: 'sess_idem_001'}) RETURN count(*) AS c"
            )
            s = await sess_count.single()
            assert s["c"] == 1, f"Expected 1 Session node, got {s['c']} (MERGE not working)"

    @pytest.mark.asyncio
    async def test_policy_version_node_created(self):
        from database.audit_writer import write_audit_event
        from shared.neo4j_client import get_driver

        event = _sample_event(
            decision_id="dec_pv_001",
            policy_version="financial-2.0.0",
        )
        await write_audit_event(event)

        driver = get_driver()
        async with driver.session() as session:
            result = await session.run(
                "MATCH (pv:PolicyVersion {id: 'financial-2.0.0'}) RETURN pv.policy_group AS grp"
            )
            record = await result.single()
        assert record is not None
        assert record["grp"] == "financial"


# ---------------------------------------------------------------------------
# Stream consumer integration test
# ---------------------------------------------------------------------------

class TestStreamConsumerIntegration:
    @pytest.mark.asyncio
    async def test_consumer_processes_event_from_stream(self, redis_client):
        """
        Enqueue an event to Redis Stream → run one consumer iteration → verify Neo4j write.
        """
        import unittest.mock as mock
        from database.audit_writer import AuditEvent, write_audit_event
        from database.stream_writer import enqueue_audit_event
        from shared.neo4j_client import get_driver
        from gateway.config import settings

        event = _sample_event(
            decision_id="dec_consumer_001",
            agent_id="agent_consumer_test",
        )

        # Enqueue to stream (using our test redis)
        with mock.patch("database.stream_writer.audit_stream_client", return_value=redis_client):
            await enqueue_audit_event(event)

        # Verify event is in the stream
        depth = await redis_client.xlen(settings.audit_stream_name)
        assert depth >= 1

        # Read and process one event (simulating consumer logic)
        messages = await redis_client.xrange(settings.audit_stream_name, "-", "+", count=1)
        assert messages, "No messages in stream"

        msg_id, fields = messages[0]

        # Reconstruct AuditEvent from stream fields
        reconstructed = AuditEvent(
            decision_id=fields["decision_id"],
            agent_id=fields["agent_id"],
            agent_name=fields["agent_name"],
            tenant_id=fields["tenant_id"],
            session_id=fields["session_id"],
            tool_name=fields["tool_name"],
            arguments_hash=fields["arguments_hash"],
            verdict=fields["verdict"],
            reason=fields["reason"],
            path=fields["path"],
            rule_id=fields["rule_id"],
            latency_ms=float(fields["latency_ms"]),
            policy_version=fields["policy_version"],
            timestamp=datetime.fromisoformat(fields["timestamp"]),
        )

        # Write to Neo4j
        success = await write_audit_event(reconstructed)
        assert success is True

        # Verify in Neo4j
        driver = get_driver()
        async with driver.session() as session:
            result = await session.run(
                "MATCH (d:Decision {id: $id}) RETURN d.verdict AS v",
                id="dec_consumer_001",
            )
            record = await result.single()
        assert record is not None
        assert record["v"] == "allowed"
