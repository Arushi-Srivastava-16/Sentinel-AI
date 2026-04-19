# Sentinel Operations Runbook

On-call reference for Sentinel AI Governance Platform.

**Grafana:** https://sentinel.your-domain.com:3001
**Neo4j Browser:** https://sentinel.your-domain.com:7474
**API Docs:** https://api.sentinel.your-domain.com/docs
**Prometheus:** https://sentinel.your-domain.com:9090

---

## Quick Reference — Severity Levels

| Severity | Condition | Response Time |
|----------|-----------|---------------|
| P0 | All tool calls returning 5xx; gateway down | 15 minutes |
| P1 | >30% error rate OR fast-path p95 > 100ms | 30 minutes |
| P2 | Cognitive path degraded; circuit breaker OPEN | 1 hour |
| P3 | Dashboard offline; DLQ growing but <100 events | 4 hours |

---

## Incident Runbooks

---

### RB-01: Gateway Down / 5xx Errors

**Symptoms:** `sentinel_requests_total` stops incrementing. Agents getting 500/503.

**Step 1 — Identify scope**
```bash
# Check pod status
kubectl get pods -n sentinel -l app=gateway

# Check recent events
kubectl describe deployment gateway -n sentinel

# Tail logs (last 50 lines)
kubectl logs -n sentinel -l app=gateway --tail=50 --all-containers
```

**Step 2 — Check dependencies**
```bash
# Redis
kubectl exec -n sentinel deployment/gateway -- python -c \
  "import asyncio; from shared.redis_client import ping_redis; print(asyncio.run(ping_redis()))"

# Neo4j
kubectl exec -n sentinel deployment/gateway -- python -c \
  "import asyncio; from shared.neo4j_client import ping_neo4j; print(asyncio.run(ping_neo4j()))"

# Gateway health endpoint
kubectl port-forward -n sentinel svc/gateway 8080:80 &
curl http://localhost:8080/health | jq
```

**Step 3 — Common fixes**

| Root cause | Fix |
|-----------|-----|
| OOMKilled | `kubectl patch deployment gateway -n sentinel -p '{"spec":{"template":{"spec":{"containers":[{"name":"gateway","resources":{"limits":{"memory":"1Gi"}}}]}}}}'` |
| CrashLoopBackoff | `kubectl rollout undo deployment/gateway -n sentinel` |
| Config error | Check `sentinel-secrets` secret is populated: `kubectl get secret sentinel-secrets -n sentinel -o yaml` |
| Image pull error | Verify registry credentials: `kubectl get events -n sentinel --sort-by='.lastTimestamp' \| tail -20` |

**Step 4 — Escalation**
If not resolved in 15 minutes, route all traffic to HUMAN_REVIEW mode by setting:
```bash
kubectl set env deployment/gateway SENTINEL_FORCE_HUMAN_REVIEW=true -n sentinel
```
This bypasses all LLM evaluation and returns HUMAN_REVIEW for every cognitive-path request.

---

### RB-02: Redis Down

**Symptoms:** Health check shows `redis: "down"`. Rate limiter errors in logs. Audit events not flowing.

**Grafana alert:** `sentinel_audit_dlq_depth > 0` (Neo4j writes going direct)

**Step 1 — Verify**
```bash
# Check Redis pod
kubectl get pods -n sentinel -l app=redis

# Ping from gateway
kubectl exec -n sentinel deployment/gateway -- redis-cli -u $REDIS_URL ping
```

**Step 2 — Behaviour under Redis outage**

Sentinel is designed to **fail open** when Redis is down:

| Component | Behaviour |
|-----------|---------|
| Rate limiter | **FAILS OPEN** — all requests allowed (no throttling) |
| Judge cache | Bypassed — every cognitive request goes to Ollama |
| Circuit breaker | Returns CLOSED (safe default) |
| Audit stream | Falls back to **direct Neo4j write** (synchronous, slower) |
| WebSocket | Fan-out stops — dashboard shows "WS Disconnected" |

**This means the gateway continues serving requests. No immediate P0.**

**Step 3 — Restore Redis**
```bash
# For Redis StatefulSet (production)
kubectl rollout restart statefulset/redis -n sentinel
kubectl rollout status statefulset/redis -n sentinel

# Verify DLQ is empty after recovery
kubectl exec -n sentinel deployment/gateway -- redis-cli -u $REDIS_URL \
  XLEN sentinel:audit:dlq
```

**Step 4 — Reconcile DLQ after recovery**

The audit worker automatically retries DLQ events on startup. Verify with:
```bash
kubectl rollout restart deployment/audit-worker -n sentinel
kubectl logs -n sentinel -l app=audit-worker --tail=50 | grep dlq
```

---

### RB-03: Neo4j Down

**Symptoms:** Health check shows `neo4j: "down"`. Audit events accumulating in Redis Stream.

**Grafana alert:** `sentinel_audit_dlq_depth > 100`

**Step 1 — Verify**
```bash
kubectl get pods -n sentinel -l app=neo4j

# Check Neo4j logs
kubectl logs -n sentinel -l app=neo4j --tail=100
```

**Step 2 — Behaviour under Neo4j outage**

| Component | Behaviour |
|-----------|---------|
| Tool call evaluation | **Unaffected** — decisions continue (Redis-backed) |
| Audit writes | Events queue in Redis Stream (`sentinel:audit:events`) |
| Dashboard graph view | Shows stale data / empty |
| Compliance queries | Unavailable |

**Redis Stream can buffer ~100k events (≈ 1GB). Typical ingestion is ~10 events/s → ~2.7 hours buffer.**

**Step 3 — Restore Neo4j**
```bash
kubectl rollout restart statefulset/neo4j -n sentinel
kubectl rollout status statefulset/neo4j -n sentinel

# Verify connectivity
kubectl port-forward -n sentinel svc/neo4j 7474:7474 7687:7687 &
curl http://localhost:7474/  # Should return Neo4j browser HTML
```

**Step 4 — Drain the backlog**

After Neo4j recovers, the audit worker automatically drains the stream. Monitor:
```bash
# Watch stream depth decrease
watch -n 5 'kubectl exec -n sentinel deployment/gateway -- \
  redis-cli -u $REDIS_URL XLEN sentinel:audit:events'
```

**Step 5 — Neo4j disk full**
```bash
# Check disk usage
kubectl exec -n sentinel statefulset/neo4j -- df -h /data

# Increase PVC size (requires storage class that supports expansion)
kubectl patch pvc neo4j-data -n sentinel -p '{"spec":{"resources":{"requests":{"storage":"100Gi"}}}}'
```

---

### RB-04: Ollama Down / Circuit Breaker OPEN

**Symptoms:** `sentinel_circuit_breaker_state{service="ollama"} == 1`. Dashboard shows red circuit breaker badge. All cognitive-path decisions returning `HUMAN_REVIEW`.

**Grafana alert:** `sentinel_circuit_breaker_state{service="ollama"} > 0 for 2m`

**Step 1 — Verify**
```bash
# Check Ollama pod
kubectl get pods -n sentinel -l app=ollama

# Test Ollama directly
kubectl port-forward -n sentinel svc/ollama 11434:11434 &
curl http://localhost:11434/api/tags | jq '.models[].name'
```

**Step 2 — Behaviour under Ollama outage**

- All cognitive-path tool calls → `HUMAN_REVIEW` (immediate, no latency penalty)
- Fast-path decisions (denylist, rate limit) **unaffected**
- Tier 3 (Claude Haiku via Anthropic API) **still works** — circuit breaker only gates Tier 1

**Step 3 — Restart Ollama**
```bash
kubectl rollout restart deployment/ollama -n sentinel
kubectl rollout status deployment/ollama -n sentinel

# Verify model is loaded
kubectl exec -n sentinel deployment/ollama -- ollama list
# If model missing:
kubectl exec -n sentinel deployment/ollama -- ollama pull llama3.2:3b
```

**Step 4 — Manually reset circuit breaker**

The circuit breaker auto-transitions to HALF_OPEN after `CIRCUIT_BREAKER_RESET_TIMEOUT_SECONDS` (default 30s). To force immediate reset:
```bash
kubectl exec -n sentinel deployment/gateway -- python -c "
import asyncio
from shared.redis_client import rate_limit_client
async def reset():
    r = rate_limit_client()
    await r.delete('sentinel:circuit_breaker:ollama')
    await r.delete('sentinel:circuit_breaker:ollama:failures')
    await r.aclose()
    print('Circuit breaker reset.')
asyncio.run(reset())
"
```

**Step 5 — Ollama GPU OOM**
```bash
# Check GPU memory
kubectl exec -n sentinel deployment/ollama -- nvidia-smi --query-gpu=memory.used,memory.free --format=csv

# Restart to free GPU memory
kubectl rollout restart deployment/ollama -n sentinel
```

---

### RB-05: Audit DLQ Growing

**Symptoms:** `sentinel_audit_dlq_depth > 100` for >5 minutes.

**Step 1 — Identify why events are failing**
```bash
kubectl logs -n sentinel -l app=audit-worker --tail=200 | grep -E "(dlq|error|fail)"
```

**Common causes:**
- Neo4j schema mismatch (see RB-03)
- Malformed event payload (programming error)
- Neo4j connection pool exhausted

**Step 2 — Inspect DLQ events**
```bash
kubectl exec -n sentinel deployment/audit-worker -- python -c "
import asyncio
from shared.redis_client import audit_stream_client
async def inspect():
    r = audit_stream_client()
    events = await r.xrange('sentinel:audit:dlq', '-', '+', count=5)
    for id, data in events:
        print(id, data)
    await r.aclose()
asyncio.run(inspect())
"
```

**Step 3 — Reprocess DLQ**
```bash
# Move events back to main stream for reprocessing
kubectl exec -n sentinel deployment/audit-worker -- python -c "
import asyncio
from shared.redis_client import audit_stream_client
async def requeue():
    r = audit_stream_client()
    events = await r.xrange('sentinel:audit:dlq', '-', '+')
    for msg_id, data in events:
        await r.xadd('sentinel:audit:events', data)
        await r.xdel('sentinel:audit:dlq', msg_id)
    print(f'Requeued {len(events)} events.')
    await r.aclose()
asyncio.run(requeue())
"
```

---

### RB-06: High Memory Usage — Gateway Pods

**Symptoms:** Grafana shows memory > 80% for >5 minutes. Possible OOMKill events.

**Step 1 — Identify which pods**
```bash
kubectl top pods -n sentinel -l app=gateway --sort-by=memory
```

**Step 2 — Common causes + fixes**

| Cause | Fix |
|-------|-----|
| WebSocket connection leak | Check `sentinel_websocket_connections_active` metric; restart gateway if >100 connections |
| Judge result cache not evicting | Verify Redis DB3 has `maxmemory-policy allkeys-lru` |
| asyncio task accumulation | Check for stuck cognitive path tasks with: `kubectl exec -n sentinel deployment/gateway -- python -c "import asyncio; print(len(asyncio.all_tasks()))"` |

**Step 3 — Rolling restart (zero downtime)**
```bash
kubectl rollout restart deployment/gateway -n sentinel
kubectl rollout status deployment/gateway -n sentinel
```

---

### RB-07: Policy Activation Failure

**Symptoms:** `POST /v1/policies/{group}/activate` returns 500 or 422.

**Step 1 — Verify policy file exists**
```bash
kubectl exec -n sentinel deployment/gateway -- ls /app/policies/examples/
# Should show: financial-v1.yaml, financial-v2.yaml, etc.
```

**Step 2 — Validate policy YAML**
```bash
kubectl exec -n sentinel deployment/gateway -- python -c "
from policies.loader import list_available_policies
import asyncio
policies = asyncio.run(list_available_policies())
print(policies)
"
```

**Step 3 — Check Redis for active policy**
```bash
kubectl exec -n sentinel deployment/gateway -- python -c "
import asyncio
from shared.redis_client import rate_limit_client
async def check():
    r = rate_limit_client()
    keys = await r.keys('policy:active:*')
    for k in keys:
        v = await r.get(k)
        print(k, '->', v)
    await r.aclose()
asyncio.run(check())
"
```

---

## Useful Commands Reference

### Logs
```bash
# All sentinel logs (structured JSON — pipe to jq)
kubectl logs -n sentinel -l app=gateway --tail=100 | jq 'select(.level == "error")'

# Cognitive path decisions only
kubectl logs -n sentinel -l app=gateway | jq 'select(.event == "cognitive_evaluation_complete")'

# Rate limit hits
kubectl logs -n sentinel -l app=gateway | jq 'select(.verdict == "blocked" and .rule_id == "rate_limit")'
```

### Neo4j Compliance Queries
```cypher
-- Recent BLOCKED decisions (last 30 min)
MATCH (tc:ToolCall)-[:RESULTED_IN]->(d:Decision {verdict: "blocked"})
WHERE d.timestamp_ns > (timestamp() - 1800000) * 1000000
RETURN tc.tool_name, d.reason, d.path, d.policy_version
ORDER BY d.timestamp_ns DESC LIMIT 50;

-- Agents by block rate (last 1 hour)
MATCH (a:Agent)-[:INITIATED]->(:Session)-[:CONTAINS]->(:ToolCall)-[:RESULTED_IN]->(d:Decision)
WHERE d.timestamp_ns > (timestamp() - 3600000) * 1000000
WITH a.id AS agent, count(*) AS total,
     sum(CASE WHEN d.verdict = "blocked" THEN 1 ELSE 0 END) AS blocked
RETURN agent, blocked, total, toFloat(blocked)/total AS block_rate
ORDER BY block_rate DESC LIMIT 20;

-- Circuit breaker OPEN periods
MATCH (d:Decision {reason: "Evaluation failed"})
RETURN date(datetime({epochMillis: d.timestamp_ns / 1000000})) AS day,
       count(*) AS fallback_decisions
ORDER BY day DESC;
```

### Kubernetes
```bash
# Force scale-up immediately (before HPA kicks in)
kubectl scale deployment/gateway -n sentinel --replicas=5

# Check HPA status
kubectl describe hpa gateway -n sentinel

# Drain a node gracefully
kubectl drain <node-name> --ignore-daemonsets --delete-emptydir-data

# Get all events in namespace (sorted)
kubectl get events -n sentinel --sort-by='.lastTimestamp'
```

---

## Escalation Path

1. **On-call engineer** (PagerDuty rotation) — P0/P1 incidents
2. **Platform team** — Infrastructure issues (Kubernetes, Redis, Neo4j)
3. **ML/AI team** — Judge model quality issues, Ollama problems
4. **Security team** — Policy bypass attempts, anomalous agent behaviour

## SLO Reference

| Metric | Target | Alert Threshold |
|--------|--------|----------------|
| Fast path p95 latency | < 15ms | > 50ms for 2m |
| Cognitive path p95 latency | < 800ms | > 2s for 5m |
| Error rate (5xx) | < 0.1% | > 1% for 1m |
| Audit event processing lag | < 30s | > 300s |
| Gateway availability | 99.9% | Any 5m window < 99% |
