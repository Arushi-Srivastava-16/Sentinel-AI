# Sentinel — AI Agent Governance Platform

**Real-time policy enforcement and audit layer for AI agents. Intercepts every tool call, evaluates it against versioned policies, and produces a tamper-evident audit trail — in under 10ms for deterministic rules, under 500ms for LLM-evaluated decisions.**

[![Python](https://img.shields.io/badge/Python-3.12%2B-blue?style=flat-square&logo=python)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.104%2B-009688?style=flat-square&logo=fastapi)](https://fastapi.tiangolo.com/)
[![Neo4j](https://img.shields.io/badge/Neo4j-Audit%20Graph-008CC1?style=flat-square&logo=neo4j)](https://neo4j.com/)
[![Redis](https://img.shields.io/badge/Redis-Rate%20Limiting-DC382D?style=flat-square&logo=redis)](https://redis.io/)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?style=flat-square&logo=docker)](https://docker.com/)
[![React](https://img.shields.io/badge/React-Dashboard-61DAFB?style=flat-square&logo=react)](https://react.dev/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow?style=flat-square)](LICENSE)

[**Quick Start**](#quick-start-5-minutes) · [**Architecture**](#architecture) · [**Demo Scenarios**](#demo-scripts) · [**API Reference**](#api-reference) · [**SDK**](#sdk-usage)

---

## How It Works

```
Agent → [sentinel-sdk] → Sentinel Gateway → Fast Path  (<10ms)  → ALLOWED / BLOCKED
                                          ↘ Cognitive Path (<500ms) → ALLOWED / BLOCKED / HUMAN_REVIEW
                                                    ↓
                                              Neo4j Audit Trail
                                                    ↓
                                          Live React Dashboard (WebSocket)
```

---

## Prerequisites

| Tool | Version | Purpose |
|---|---|---|
| Docker + Docker Compose | v2.20+ | Run all services |
| Python | 3.12+ | Run demo scripts |
| Node.js | 20+ | Dashboard (dev mode only) |
| Ollama | latest | Local LLM (Llama 3.2 3B) |

---

## Quick Start (5 minutes)

### 1. Clone and configure

```bash
git clone https://github.com/Arushi-Srivastava-16/Sentinel-AI.git
cd Sentinel-AI
cp .env.example .env
# Edit .env — at minimum set:
#   SENTINEL_ADMIN_KEY=snl_your_secret_admin_key_here
#   OPENAI_API_KEY=your_openai_key
#   OPENAI_MODEL=gpt-4o-mini
```

### 2. Start the full stack

```bash
docker compose -f docker-compose.yml up -d --build
```

This starts:

- **Redis** (port 6379) — rate limiting, audit queue, WebSocket fan-out, judge cache
- **Neo4j** (port 7474/7687) — audit graph database
- **Ollama** (port 11434) — local Llama 3.2 3B judge
- **Sentinel Gateway** (port 8000) — FastAPI governance layer
- **Audit Worker** — Redis Streams → Neo4j consumer
- **Dashboard** (port 3000) — React live monitoring UI

### 3. Open the dashboard

Navigate to http://localhost:3000

You should see:
- **Status bar** (top): green WebSocket dot, Ollama circuit breaker = CLOSED
- **Metrics cards**: 0 total decisions, 0 blocked, 0 allowed
- **Event feed**: empty (no agents running yet)
- **Graph viewer**: empty canvas

### 4. Run the demo scenarios

Open three terminal tabs. In each, run one scenario:

```bash
# Tab 1 — Rogue Exfiltrator
python agents/demo_a.py

# Tab 2 — Rate Limit Abuser
python agents/demo_b.py

# Tab 3 — Policy Version Rollback
python agents/demo_c.py
```

Watch the dashboard light up in real time.

---

## Demo Scripts

### Demo A — "The Rogue Exfiltrator"

**Narrative:** A financial agent tries to exfiltrate data. Sentinel catches it.

```bash
python agents/demo_a.py
```

| # | Tool Call | Expected | Path | Why |
|---|---|---|---|---|
| 1 | `read_file("/etc/passwd")` | BLOCKED | Fast | Denylist: system file access |
| 2 | `send_email(to="rival@competitor.com", body=<PII>)` | BLOCKED | Cognitive | LLM: intent mismatch + exfiltration pattern |
| 3 | `write_file("/tmp/export.csv")` | ALLOWED | Fast | Low-risk path, no policy violation |
| 4 | `database_query("SELECT * FROM users")` | BLOCKED | Cognitive | LLM: PII bulk extraction |

**Dashboard narration:**
1. First event appears instantly (red BLOCKED badge) — fast path, denylist hit
2. Third call shows green ALLOWED — graph node appears linked to the agent
3. Second and fourth calls show red after 200–500ms — cognitive path LLM evaluation
4. Click any event in the feed to see the Neo4j graph expand

---

### Demo B — "The Rate Limit Abuser"

**Narrative:** A web scraper fires 200 requests. Sentinel enforces per-agent quotas.

```bash
python agents/demo_b.py
```

- Calls 1–50: green ALLOWED (within 60-second burst quota)
- Calls 51–200: red BLOCKED ("Rate limit exceeded") in <1ms each
- Other agents running simultaneously: unaffected (per-agent isolation)

```
[  1/ 50] web_fetch → ALLOWED     (2ms)
[  2/ 50] web_fetch → ALLOWED     (1ms)
...
[ 51/200] web_fetch → BLOCKED     (0ms)  Rate limit exceeded (50/50 tokens used)
```

---

### Demo C — "The Policy Version Rollback"

**Narrative:** A code executor runs fine under v1. Admin deploys stricter v2. Same call changes verdict live.

```bash
python agents/demo_c.py
```

1. Policy `financial-v1` is active
2. `execute_code(language="python", service="payments")` → **ALLOWED** (v1 has no code restriction)
3. Script pauses: *"Admin is activating financial-v2..."*
4. `POST /v1/policies/financial/activate {"version": "2.0.0"}` — takes effect immediately
5. Same `execute_code()` call → **HUMAN_REVIEW** (v2 adds human-review rule for code execution)
6. Neo4j compliance query: "3 decisions made under v1 would be restricted by v2"

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     Sentinel Gateway (FastAPI)               │
│                                                             │
│  POST /v1/tool-calls                                        │
│    │                                                        │
│    ├─ Auth middleware (API key validation → Redis)          │
│    ├─ Rate limiter (token bucket Lua script → Redis DB0)    │
│    ├─ Classifier (heuristic: fast vs cognitive)             │
│    │                                                        │
│    ├─ FAST PATH (p95 < 10ms)                               │
│    │    Denylist → Allowlist → Threshold → Regex            │
│    │    → Decision (200 sync)                               │
│    │                                                        │
│    └─ COGNITIVE PATH (p95 < 500ms)                         │
│         Tier 1: Llama-3.2-3B (Ollama, 3s budget)          │
│         Tier 3: Claude Haiku (Anthropic API, 15s budget)   │
│         Circuit breaker: Redis-shared OPEN/CLOSED state     │
│         → Decision (202 async + poll endpoint)             │
│                                                             │
│  Audit pipeline: Redis Streams → Consumer → Neo4j           │
│  WebSocket: Redis Pub/Sub → Dashboard (fan-out)             │
└─────────────────────────────────────────────────────────────┘
```

### Redis Database Layout

| DB | Purpose | Key Pattern | Eviction |
|---|---|---|---|
| 0 | Rate limiting | `{tenant}:rate:{agent_id}:{window}` | volatile-ttl |
| 1 | Audit stream | `sentinel:audit:events` (Stream) | noeviction |
| 2 | WebSocket fan-out | `sentinel:dashboard:events` (Pub/Sub) | allkeys-lru |
| 3 | Judge cache | `judge:cache:{sha256}` | allkeys-lru |

### Neo4j Graph Schema

```
(:Agent)-[:INITIATED]->(:Session)
(:Session)-[:CONTAINS]->(:ToolCall)
(:ToolCall)-[:RESULTED_IN]->(:Decision)
(:Decision)-[:EVALUATED_UNDER]->(:PolicyVersion)
(:Decision)-[:TRIGGERED_RULE]->(:Rule)
(:Decision)-[:JUDGED_BY]->(:JudgeTier)
(:ToolCall)-[:FOLLOWS]->(:ToolCall)   // temporal chain
```

**Compliance queries** (run in Neo4j Browser at http://localhost:7474):

```cypher
-- Which v1-ALLOWED decisions would v2 block?
MATCH (d:Decision)-[:EVALUATED_UNDER]->(pv:PolicyVersion {version: "1.0.0"})
WHERE d.verdict = "allowed"
MATCH (d)-[:TRIGGERED_RULE]->(r:Rule)
WHERE NOT EXISTS {
  MATCH (r2:Rule)<-[:CONTAINS]-(:PolicyVersion {version: "2.0.0"})
  WHERE r2.name = r.name
}
RETURN d.id, d.verdict, r.name, d.timestamp_ns
ORDER BY d.timestamp_ns DESC;

-- All tool calls by a specific agent in the last hour
MATCH (a:Agent {id: "agent_abc"})-[:INITIATED]->(:Session)-[:CONTAINS]->(tc:ToolCall)
WHERE tc.timestamp_ns > (timestamp() - 3600000) * 1000000
MATCH (tc)-[:RESULTED_IN]->(d:Decision)
RETURN tc.tool_name, d.verdict, d.latency_ms, d.timestamp_ns
ORDER BY tc.timestamp_ns DESC;
```

---

## API Reference

Full interactive docs: http://localhost:8000/docs

| Endpoint | Method | Auth | Description |
|---|---|---|---|
| `/v1/tool-calls` | POST | Agent key | Submit a tool call for evaluation |
| `/v1/decisions/{id}` | GET | Agent key | Poll for async cognitive path result |
| `/v1/agents` | POST | Admin key | Register a new agent, get API key |
| `/v1/agents/{id}` | GET | Admin key | Get agent info |
| `/v1/policies` | GET | Admin key | List all policy versions |
| `/v1/policies/{group}/activate` | POST | Admin key | Activate a policy version (live) |
| `/ws/dashboard` | WS | Agent key (query param) | Live event stream |
| `/health` | GET | None | Service health (Redis, Neo4j, Ollama) |
| `/metrics` | GET | None | Prometheus metrics |

### Tool Call Request

```json
{
  "tool_name": "execute_payment",
  "arguments": {
    "amount": 75000,
    "currency": "USD",
    "recipient": "vendor_abc"
  },
  "context": {
    "task_description": "Pay Q1 invoice per clause 4.2",
    "conversation_history": [],
    "source_documents": ["contract_q1.pdf"]
  }
}
```

### Decision Response

```json
{
  "decision_id": "dec_a1b2c3",
  "verdict": "blocked",
  "reason": "Payment amount $75,000 exceeds policy threshold of $10,000 without explicit authorization.",
  "path": "cognitive_path",
  "latency_ms": 312.4,
  "policy_version": "financial-1.0.0",
  "confidence": 0.94,
  "rate_limit": {
    "tokens_remaining": 47,
    "reset_at": 1709123456
  }
}
```

---

## SDK Usage

```python
import asyncio
from sentinel_sdk.client import AgentClient

async def main():
    async with AgentClient(
        gateway_url="http://localhost:8000",
        api_key="snl_your_agent_key",
        agent_id="agent_abc",
    ) as sentinel:
        decision = await sentinel.check(
            tool_name="execute_payment",
            arguments={"amount": 75000, "currency": "USD", "recipient": "vendor_abc"},
            context={"task_description": "Pay Q1 invoice per clause 4.2"},
        )
        if decision.is_allowed:
            result = execute_payment(amount=75000, currency="USD", recipient="vendor_abc")
        else:
            raise BlockedBySentinel(decision.reason)

asyncio.run(main())
```

---

## Project Structure

```
sentinel/
├── gateway/              # FastAPI governance layer
│   ├── auth/             # API key validation → Redis
│   ├── classifier/       # Heuristic fast/cognitive router
│   ├── cognitive_path/   # Async decision handler
│   ├── fast_path/        # Denylist, allowlist, rate limiter
│   ├── middleware/        # Auth middleware, AgentContext
│   ├── models/           # Pydantic request/response models
│   ├── routes/           # HTTP + WebSocket route handlers
│   └── websocket/        # ConnectionManager, Redis Pub/Sub bridge
├── judge/                # LLM judge cascade
│   ├── tier1.py          # Llama-3.2-3B (Ollama)
│   ├── tier3.py          # Claude Haiku (Anthropic API)
│   ├── cascade.py        # Tier selection + fallback logic
│   └── circuit_breaker.py # Redis-backed OPEN/CLOSED state
├── database/             # Neo4j audit pipeline
│   ├── audit_writer.py   # Cypher write logic
│   ├── stream_writer.py  # Redis Streams producer
│   └── stream_consumer.py # Consumer worker
├── policies/             # Versioned YAML policy files
│   ├── schema.yaml       # Policy document JSON Schema
│   ├── loader.py         # Active version resolution (Redis-backed)
│   ├── engine.py         # Rule evaluation
│   └── examples/         # financial-v1.yaml, financial-v2.yaml
├── shared/               # Redis + Neo4j client factories
├── sentinel-sdk/         # Agent-side SDK (thin httpx wrapper)
├── agents/               # Demo scripts
├── dashboard/            # React + TypeScript dashboard
│   └── src/
│       ├── components/   # EventFeed, GraphViewer, PolicyPanel
│       ├── hooks/        # useWebSocket, useDecisions
│       └── store/        # Zustand event store (500-event ring buffer)
├── tests/
│   ├── unit/             # Pure function tests (no I/O)
│   ├── integration/      # testcontainers (Redis + Neo4j)
│   └── e2e/              # Full stack tests
├── docker-compose.yml
├── docker-compose.dev.yml
└── .env.example
```

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `SENTINEL_ADMIN_KEY` | ✅ | Admin API key for management endpoints |
| `OPENAI_API_KEY` | ✅ | OpenAI key for LLM judge |
| `OPENAI_MODEL` | ✅ | Model to use (e.g. `gpt-4o-mini`) |
| `FORCE_COGNITIVE_PATH` | optional | Force all decisions through LLM |
| `JUDGE_FORCE_TIER3_OPENAI` | optional | Skip Ollama, use OpenAI directly |
| `NEO4J_URI` | optional | Neo4j connection URI (default: bolt://localhost:7687) |
| `REDIS_URL` | optional | Redis URL (default: redis://localhost:6379) |

---

## License

MIT © 2026 Arushi Srivastava
```
