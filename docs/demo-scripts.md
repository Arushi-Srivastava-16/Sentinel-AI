# Sentinel — Demo Scripts Reference

Step-by-step walkthrough of all three demo scenarios.
Run these after `make dev-up` and after the gateway is running.

---

## Prerequisites

```bash
make dev-up          # Start Neo4j, Redis, Ollama, Langfuse
make run-gateway     # In a separate terminal
# Open dashboard: npm run dev in dashboard/
# Open Neo4j browser: http://localhost:7474
# Open Langfuse: http://localhost:3001
```

Register demo agents (one-time setup):

```bash
# Register demo agents using the admin key
curl -s -X POST http://localhost:8000/v1/agents \
  -H "X-Sentinel-Agent-Key: $SENTINEL_ADMIN_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name":"demo_a_agent","policy_group":"financial"}' | tee /tmp/agent_a.json

export DEMO_A_API_KEY=$(cat /tmp/agent_a.json | python3 -c "import sys,json; print(json.load(sys.stdin)['api_key'])")
export DEMO_A_AGENT_ID=$(cat /tmp/agent_a.json | python3 -c "import sys,json; print(json.load(sys.stdin)['agent_id'])")

# Repeat for agents B and C ...
```

---

## Scenario A — "The Rogue Exfiltrator"

**Narrative:** A financial analyst agent starts legitimately but then attempts
system file reads, data exfiltration via email, bulk CSV export, and a PII
database query.

**What to show on dashboard while running:**
1. Live event feed: watch verdicts come in one by one
2. Point out: Step 1 resolves in ~5ms (denylist, fast path, no LLM)
3. Point out: Step 2 takes ~300ms (cognitive path, LLM judge running)
4. After run: click agent in graph view, trace the full decision chain

**Run:**
```bash
make demo-a
```

**Expected output:**
```
Step 1: read_file(/etc/passwd)        → BLOCKED       via fast_path     (~5ms)
Step 2: send_email(competitor@...)    → BLOCKED       via cognitive_path (~350ms)
Step 3: write_file(/tmp/dump.csv)     → HUMAN_REVIEW  via fast_path     (~8ms)
Step 4: database_query(SELECT users)  → BLOCKED       via cognitive_path (~300ms)

Results: 4/4 correct verdicts ✓
```

**Neo4j query to show after:**
```cypher
MATCH (a:Agent {id: $agent_id})-[:INITIATED]->(s:Session)-[:CONTAINS]->(tc:ToolCall)
      -[:RESULTED_IN]->(d:Decision)
RETURN tc.tool_name, d.verdict, d.path, d.latency_ms, d.reason
ORDER BY tc.timestamp_ns
```

---

## Scenario B — "The Rate Limit Abuser"

**Narrative:** A web scraper agent sends 200 tool calls as fast as possible.
Sentinel's token bucket allows the first 50, then blocks the rest.

**What to show on dashboard while running:**
1. Rate limit heatmap: watch it fill up to red
2. Requests-per-second counter
3. After run: show that a second agent (demo_a) is unaffected (agent isolation)

**Run:**
```bash
make demo-b
```

**Expected output:**
```
[  1/200] ✓ allowed=1  blocked=0  rps=~200
...
[ 50/200] ✓ allowed=50 blocked=0  rps=~200
[ 51/200] ✗ allowed=50 blocked=1  rps=~200
...
[200/200] ✗ allowed=50 blocked=150 rps=~200

Rate limit enforcement: PASS ✓
```

---

## Scenario C — "The Policy Version Rollback"

**Narrative:** A code executor agent is running normally under policy v1.
An admin activates v2 mid-run. The same tool call that was ALLOWED is now
HUMAN_REVIEW — without restarting the gateway.

**What to show on dashboard while running:**
1. Pause at step 3 — show the policy panel, click "Activate v2.0.0"
2. Watch the policy version change fire as a WebSocket event
3. After step 4 — show Neo4j graph: both decisions visible, different policy versions

**Run:**
```bash
make demo-c
```

**Expected output:**
```
Step 1: Activate financial policy v1.0.0 ✓
Step 2: execute_code → ALLOWED  [policy: financial-1.0.0]  PASS ✓
Step 3: Admin activates v2.0.0 (BREAKING: execute_code → HUMAN_REVIEW)
Step 4: execute_code → HUMAN_REVIEW  [policy: financial-2.0.0]  PASS ✓

Policy rollback demo: PASS ✓
```

**Compliance query to run live:**
```cypher
MATCH (tc:ToolCall)-[:RESULTED_IN]->(d:Decision)
      -[:EVALUATED_UNDER]->(pv:PolicyVersion {version: "1.0.0"})
WHERE d.verdict = "ALLOWED"
  AND tc.tool_name = "execute_code"
RETURN tc.id, d.verdict, pv.version AS policy
ORDER BY tc.timestamp_ns DESC
```

---

## Full End-to-End (automated)

```bash
# All three scenarios as pytest E2E tests
make test-e2e
```
