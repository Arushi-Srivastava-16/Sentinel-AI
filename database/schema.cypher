// =============================================================================
// Sentinel — Neo4j Graph Schema
// Run this file once on a fresh database to create all constraints and indexes.
// Usage: cypher-shell -u neo4j -p <password> -f database/schema.cypher
// =============================================================================

// -----------------------------------------------------------------------------
// Constraints — enforce uniqueness and required properties
// -----------------------------------------------------------------------------

// Agent: each agent has a unique ID within a tenant
CREATE CONSTRAINT agent_id_unique IF NOT EXISTS
FOR (a:Agent) REQUIRE a.id IS UNIQUE;

CREATE CONSTRAINT agent_name_tenant_unique IF NOT EXISTS
FOR (a:Agent) REQUIRE (a.name, a.tenant_id) IS UNIQUE;

// Session: unique per agent
CREATE CONSTRAINT session_id_unique IF NOT EXISTS
FOR (s:Session) REQUIRE s.id IS UNIQUE;

// ToolCall: unique execution ID
CREATE CONSTRAINT tool_call_id_unique IF NOT EXISTS
FOR (tc:ToolCall) REQUIRE tc.id IS UNIQUE;

// Decision: unique decision ID
CREATE CONSTRAINT decision_id_unique IF NOT EXISTS
FOR (d:Decision) REQUIRE d.id IS UNIQUE;

// PolicyVersion: unique per (policy_group, version) tuple
CREATE CONSTRAINT policy_version_unique IF NOT EXISTS
FOR (pv:PolicyVersion) REQUIRE (pv.policy_group, pv.version) IS UNIQUE;

// Rule: unique rule ID
CREATE CONSTRAINT rule_id_unique IF NOT EXISTS
FOR (r:Rule) REQUIRE r.id IS UNIQUE;

// JudgeTier: unique per (tier_number, model_name)
CREATE CONSTRAINT judge_tier_unique IF NOT EXISTS
FOR (jt:JudgeTier) REQUIRE (jt.tier_number, jt.model_name) IS UNIQUE;

// -----------------------------------------------------------------------------
// Indexes — optimise common query patterns
// -----------------------------------------------------------------------------

// Temporal range queries on ToolCall (most common audit query)
CREATE INDEX tool_call_timestamp IF NOT EXISTS
FOR (tc:ToolCall) ON (tc.timestamp_ns);

// Filter decisions by verdict
CREATE INDEX decision_verdict IF NOT EXISTS
FOR (d:Decision) ON (d.verdict);

// Filter decisions by path (fast_path vs cognitive_path)
CREATE INDEX decision_path IF NOT EXISTS
FOR (d:Decision) ON (d.path);

// Find all decisions for a specific policy version
CREATE INDEX decision_policy_version IF NOT EXISTS
FOR (d:Decision) ON (d.policy_version_id);

// Agent lookup by tenant
CREATE INDEX agent_tenant IF NOT EXISTS
FOR (a:Agent) ON (a.tenant_id);

// Session lookup by agent
CREATE INDEX session_agent IF NOT EXISTS
FOR (s:Session) ON (s.agent_id);

// Session temporal range queries
CREATE INDEX session_started_at IF NOT EXISTS
FOR (s:Session) ON (s.started_at);

// Policy lookup by group and active status
CREATE INDEX policy_group IF NOT EXISTS
FOR (pv:PolicyVersion) ON (pv.policy_group);

CREATE INDEX policy_active IF NOT EXISTS
FOR (pv:PolicyVersion) ON (pv.is_active);

// Composite: find active violations by agent in time window
CREATE INDEX tool_call_agent_timestamp IF NOT EXISTS
FOR (tc:ToolCall) ON (tc.agent_id, tc.timestamp_ns);

// -----------------------------------------------------------------------------
// Seed: JudgeTier nodes (static — these don't change at runtime)
// -----------------------------------------------------------------------------

MERGE (t1:JudgeTier {tier_number: 1})
  ON CREATE SET
    t1.model_name    = "llama3.2:3b",
    t1.description   = "Local Ollama — fast intent classification",
    t1.timeout_ms    = 3000,
    t1.created_at    = datetime();

MERGE (t3:JudgeTier {tier_number: 3})
  ON CREATE SET
    t3.model_name    = "claude-haiku-4-5-20251001",
    t3.description   = "Anthropic Claude Haiku — fallback for high-stakes / escalations",
    t3.timeout_ms    = 15000,
    t3.created_at    = datetime();

// -----------------------------------------------------------------------------
// Seed: Default tenant
// -----------------------------------------------------------------------------

MERGE (tenant:Tenant {id: "default"})
  ON CREATE SET
    tenant.name       = "Default Tenant",
    tenant.created_at = datetime();

// =============================================================================
// Schema reference (comment — not executable)
// =============================================================================
//
// NODE TYPES
// ----------
// (:Agent        {id, name, tenant_id, policy_group, registered_at, api_key_hash})
// (:Session      {id, agent_id, tenant_id, started_at, ended_at, request_count})
// (:ToolCall     {id, agent_id, tenant_id, tool_name, arguments_hash,
//                 raw_request_sha256, timestamp_ns, session_id})
// (:Decision     {id, verdict, reason, confidence, path, latency_ms,
//                 timestamp_ns, policy_version_id, judge_tier})
// (:PolicyVersion{id, policy_group, version, checksum, effective_from,
//                 effective_until, is_active, parent_version})
// (:Rule         {id, name, description, severity, policy_version_id})
// (:JudgeTier    {tier_number, model_name, description, timeout_ms, created_at})
// (:Tenant       {id, name, created_at})
//
// RELATIONSHIP TYPES
// ------------------
// (:Agent)        -[:INITIATED]->       (:Session)
// (:Session)      -[:CONTAINS]->        (:ToolCall)
// (:ToolCall)     -[:RESULTED_IN]->     (:Decision)
// (:Decision)     -[:EVALUATED_UNDER]-> (:PolicyVersion)
// (:Decision)     -[:TRIGGERED_RULE]->  (:Rule)
// (:Decision)     -[:JUDGED_BY]->       (:JudgeTier)       // cognitive path only
// (:ToolCall)     -[:FOLLOWS]->         (:ToolCall)         // temporal chain
// (:Agent)        -[:BELONGS_TO]->      (:Tenant)
//
// KEY COMPLIANCE QUERIES
// ----------------------
// 1. Full decision chain for a session:
//    MATCH (a:Agent)-[:INITIATED]->(s:Session {id: $sid})-[:CONTAINS]->(tc:ToolCall)
//          -[:RESULTED_IN]->(d:Decision)-[:EVALUATED_UNDER]->(pv:PolicyVersion)
//    RETURN a, s, tc, d, pv ORDER BY tc.timestamp_ns
//
// 2. Decisions that would be blocked under new policy (policy diff):
//    MATCH (tc:ToolCall)-[:RESULTED_IN]->(d:Decision)-[:EVALUATED_UNDER]->(old:PolicyVersion {version: $old_v})
//    WHERE d.verdict = "ALLOWED"
//    AND NOT EXISTS {
//      MATCH (tc)-[:RESULTED_IN]->(d2:Decision)-[:EVALUATED_UNDER]->(:PolicyVersion {version: $new_v})
//      WHERE d2.verdict = "ALLOWED"
//    }
//    RETURN tc, d
//
// 3. Most-triggered rules in last 7 days:
//    MATCH (d:Decision)-[:TRIGGERED_RULE]->(r:Rule)
//    WHERE d.timestamp_ns > (timestamp() - 7*24*3600*1000) * 1000000
//    RETURN r.name, count(d) AS hits ORDER BY hits DESC LIMIT 10
//
// 4. Agents with >10 blocked decisions in last 24h:
//    MATCH (a:Agent)-[:INITIATED]->(s:Session)-[:CONTAINS]->(tc:ToolCall)
//          -[:RESULTED_IN]->(d:Decision)
//    WHERE d.verdict = "BLOCKED"
//    AND d.timestamp_ns > (timestamp() - 24*3600*1000) * 1000000
//    WITH a, count(d) AS blocks WHERE blocks > 10
//    RETURN a.name, a.id, blocks ORDER BY blocks DESC
