"""
Prometheus metrics for the Sentinel Gateway.

All metrics are defined here as module-level singletons.
Import and increment/observe them from route handlers.

Metrics defined (matching the plan):
    sentinel_requests_total{agent_id, tool_name, verdict, path}
    sentinel_request_duration_seconds{path, quantile}
    sentinel_judge_calls_total{tier, outcome}
    sentinel_circuit_breaker_state{service}
    sentinel_rate_limit_hits_total{agent_id}
    sentinel_audit_dlq_depth
    sentinel_policy_version_active{policy_group, version}
    sentinel_websocket_connections_active
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram, Info

# ---------------------------------------------------------------------------
# Request counters & latency
# ---------------------------------------------------------------------------

requests_total = Counter(
    "sentinel_requests_total",
    "Total tool call requests processed",
    labelnames=["agent_id", "tool_name", "verdict", "path"],
)

request_duration_seconds = Histogram(
    "sentinel_request_duration_seconds",
    "End-to-end latency for tool call decisions",
    labelnames=["path"],
    buckets=(0.005, 0.010, 0.025, 0.050, 0.100, 0.250, 0.500, 1.0, 2.5, 5.0, 10.0, 20.0),
)

# ---------------------------------------------------------------------------
# Judge cascade
# ---------------------------------------------------------------------------

judge_calls_total = Counter(
    "sentinel_judge_calls_total",
    "Total calls to each judge tier",
    labelnames=["tier", "outcome"],  # outcome: allowed, blocked, human_review, timeout, error
)

# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------

circuit_breaker_state = Gauge(
    "sentinel_circuit_breaker_state",
    "Circuit breaker state: 0=CLOSED, 1=OPEN, 2=HALF_OPEN",
    labelnames=["service"],  # service: ollama
)

# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

rate_limit_hits_total = Counter(
    "sentinel_rate_limit_hits_total",
    "Total requests rejected by rate limiter",
    labelnames=["agent_id"],
)

# ---------------------------------------------------------------------------
# Audit pipeline health
# ---------------------------------------------------------------------------

audit_dlq_depth = Gauge(
    "sentinel_audit_dlq_depth",
    "Number of events currently in the audit dead-letter queue",
)

# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------

policy_version_active = Gauge(
    "sentinel_policy_version_active",
    "Currently active policy version per group (value = 1 means active)",
    labelnames=["policy_group", "version"],
)

# ---------------------------------------------------------------------------
# WebSocket connections
# ---------------------------------------------------------------------------

websocket_connections_active = Gauge(
    "sentinel_websocket_connections_active",
    "Number of active WebSocket dashboard connections",
)

# ---------------------------------------------------------------------------
# Build info (static labels — useful for join in Grafana)
# ---------------------------------------------------------------------------

build_info = Info(
    "sentinel_build",
    "Static build metadata",
)
build_info.info({"version": "1.0.0", "service": "gateway"})
