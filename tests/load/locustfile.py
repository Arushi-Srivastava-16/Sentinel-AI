"""
Sentinel Gateway — Locust load test.

Targets:
  - Fast path p95 < 15ms at 500 concurrent agents
  - Cognitive path p95 < 800ms (generous — Ollama cold start)
  - Gateway must not error (5xx) under sustained load

Usage:
  # Headless, 500 users, 5-minute ramp, 10-minute run:
  locust -f tests/load/locustfile.py \\
    --headless -u 500 -r 50 -t 10m \\
    --host http://localhost:8000 \\
    --csv tests/load/results/run_$(date +%s)

  # Web UI (real-time charts):
  locust -f tests/load/locustfile.py --host http://localhost:8000

Environment variables:
  SENTINEL_LOAD_ADMIN_KEY   Admin key to register test agents (required)
  SENTINEL_LOAD_USERS       Override --users (optional)
"""

from __future__ import annotations

import os
import random
import time
import uuid
from typing import Any

from locust import HttpUser, between, events, task
from locust.runners import MasterRunner

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

ADMIN_KEY = os.getenv("SENTINEL_LOAD_ADMIN_KEY", os.getenv("SENTINEL_ADMIN_API_KEY", "snl_admin_dev_key_replace_in_production"))

# Pool of tool calls — weighted toward fast-path to stress the common case
_FAST_PATH_TOOLS = [
    ("read_file",       {"path": "/reports/q1.csv"}),
    ("read_file",       {"path": "/data/config.json"}),
    ("web_fetch",       {"url": "https://api.company.com/prices"}),
    ("list_directory",  {"path": "/data/reports"}),
    ("web_fetch",       {"url": "https://news.example.com/feed"}),
]

_COGNITIVE_PATH_TOOLS = [
    ("execute_payment", {"amount": 500, "currency": "USD", "recipient": "vendor_001"}),
    ("send_email",      {"to": "finance@company.com", "subject": "Invoice", "body": "Please process attached."}),
    ("database_query",  {"query": "SELECT id, name FROM customers WHERE active = true LIMIT 100"}),
]

_DENYLIST_TOOLS = [
    ("read_file", {"path": "/etc/passwd"}),
    ("read_file", {"path": "/etc/shadow"}),
]


# ---------------------------------------------------------------------------
# Global agent pool (shared across workers on master)
# ---------------------------------------------------------------------------

_AGENT_POOL: list[dict] = []
_POOL_SIZE = 20   # Register N agents before tests start


def _register_agent(client, name: str, policy_group: str = "financial") -> dict | None:
    with client.post(
        "/v1/agents",
        json={"name": name, "policy_group": policy_group, "tenant_id": "load_test"},
        headers={"X-Sentinel-Agent-Key": ADMIN_KEY, "Content-Type": "application/json"},
        catch_response=True,
        name="[setup] Register agent",
    ) as resp:
        if resp.status_code == 201:
            data = resp.json()
            return {"agent_id": data["agent_id"], "api_key": data["api_key"]}
        resp.failure(f"Agent registration failed: {resp.status_code} {resp.text}")
        return None


@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    """Pre-register a pool of agents before the ramp begins."""
    if isinstance(environment.runner, MasterRunner):
        return   # Only run on worker/standalone

    import httpx
    base_url = environment.host or "http://localhost:8000"
    print(f"\n[setup] Registering {_POOL_SIZE} load test agents at {base_url}...")

    for i in range(_POOL_SIZE):
        try:
            resp = httpx.post(
                f"{base_url}/v1/agents",
                json={
                    "name": f"load-test-agent-{i:03d}",
                    "policy_group": "financial",
                    "tenant_id": "load_test",
                },
                headers={"X-Sentinel-Agent-Key": ADMIN_KEY, "Content-Type": "application/json"},
                timeout=10,
            )
            if resp.status_code == 201:
                data = resp.json()
                _AGENT_POOL.append({"agent_id": data["agent_id"], "api_key": data["api_key"]})
        except Exception as e:
            print(f"[setup] Warning: agent {i} registration failed: {e}")

    print(f"[setup] Agent pool ready: {len(_AGENT_POOL)} agents registered.\n")


# ---------------------------------------------------------------------------
# Fast Path User (80% of load)
# ---------------------------------------------------------------------------

class FastPathUser(HttpUser):
    """
    Simulates agents making low-risk tool calls — exercises fast path exclusively.
    Target: p95 < 15ms at 500 concurrent users.
    """
    weight = 8
    wait_time = between(0.01, 0.1)   # 10-100ms between requests

    def on_start(self):
        if _AGENT_POOL:
            agent = random.choice(_AGENT_POOL)
            self._api_key = agent["api_key"]
            self._agent_id = agent["agent_id"]
        else:
            # Fallback: register inline (slower setup, graceful)
            result = _register_agent(self.client, f"load-fast-{uuid.uuid4().hex[:6]}")
            if result:
                self._api_key = result["api_key"]
                self._agent_id = result["agent_id"]
            else:
                self._api_key = ADMIN_KEY
                self._agent_id = "fallback"

    @task(10)
    def fast_read_file(self):
        tool_name, arguments = random.choice(_FAST_PATH_TOOLS)
        self._tool_call(tool_name, arguments, name=f"[fast] {tool_name}")

    @task(2)
    def denylist_hit(self):
        """Ensure denylist decisions are fast — not a bug, intentional stress."""
        tool_name, arguments = random.choice(_DENYLIST_TOOLS)
        self._tool_call(tool_name, arguments, name="[fast] denylist_hit")

    def _tool_call(self, tool_name: str, arguments: dict, name: str = "") -> None:
        start = time.perf_counter()
        with self.client.post(
            "/v1/tool-calls",
            json={"tool_name": tool_name, "arguments": arguments},
            headers={
                "X-Sentinel-Agent-Key": self._api_key,
                "Content-Type": "application/json",
            },
            catch_response=True,
            name=name or f"[fast] {tool_name}",
        ) as resp:
            elapsed_ms = (time.perf_counter() - start) * 1000
            if resp.status_code in (200, 429):
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("path") == "cognitive_path":
                        resp.failure(f"Expected fast_path for {tool_name}, got cognitive_path")
                    elif elapsed_ms > 100:
                        resp.failure(f"Fast path too slow: {elapsed_ms:.1f}ms (threshold 100ms)")
                    else:
                        resp.success()
                else:
                    resp.success()   # 429 is expected under rate limit — counts as success
            else:
                resp.failure(f"Unexpected status {resp.status_code}: {resp.text[:200]}")


# ---------------------------------------------------------------------------
# Cognitive Path User (15% of load)
# ---------------------------------------------------------------------------

class CognitivePathUser(HttpUser):
    """
    Simulates agents making high-risk tool calls that require LLM evaluation.
    Tests async 202 → polling flow.
    """
    weight = 2
    wait_time = between(0.5, 2.0)   # Slower — cognitive path is expensive

    def on_start(self):
        if _AGENT_POOL:
            agent = random.choice(_AGENT_POOL)
            self._api_key = agent["api_key"]
        else:
            self._api_key = ADMIN_KEY

    @task
    def cognitive_tool_call(self):
        tool_name, arguments = random.choice(_COGNITIVE_PATH_TOOLS)
        with self.client.post(
            "/v1/tool-calls",
            json={
                "tool_name": tool_name,
                "arguments": arguments,
                "context": {
                    "task_description": "Process routine financial operation per standing instructions",
                },
            },
            headers={
                "X-Sentinel-Agent-Key": self._api_key,
                "Content-Type": "application/json",
            },
            catch_response=True,
            name=f"[cognitive] {tool_name}",
        ) as resp:
            if resp.status_code == 200:
                # Sync decision — fast path caught it first
                resp.success()
            elif resp.status_code == 202:
                data = resp.json()
                decision_id = data.get("decision_id")
                poll_url = data.get("poll_url", f"/v1/decisions/{decision_id}")
                resp.success()
                # Poll for result (fire separate request — counts separately in stats)
                self._poll(poll_url)
            elif resp.status_code == 429:
                resp.success()   # Rate limited is expected
            else:
                resp.failure(f"Unexpected {resp.status_code}: {resp.text[:200]}")

    def _poll(self, poll_url: str, max_attempts: int = 20) -> None:
        for _ in range(max_attempts):
            time.sleep(0.5)
            with self.client.get(
                poll_url,
                headers={
                    "X-Sentinel-Agent-Key": self._api_key,
                    "Content-Type": "application/json",
                },
                catch_response=True,
                name="[cognitive] poll_decision",
            ) as pr:
                if pr.status_code == 200:
                    data = pr.json()
                    if data.get("status") != "pending":
                        pr.success()
                        return
                    pr.success()   # still pending — normal
                elif pr.status_code == 404:
                    pr.failure("Decision not found during poll")
                    return
                else:
                    pr.failure(f"Poll error {pr.status_code}")
                    return


# ---------------------------------------------------------------------------
# Burst Rate Limit User (5% of load)
# ---------------------------------------------------------------------------

class BurstUser(HttpUser):
    """
    Simulates a misbehaving agent sending rapid bursts to test rate limiter isolation.
    Expects high 429 rate — not a failure condition.
    """
    weight = 1
    wait_time = between(0.001, 0.005)   # Very aggressive — nearly no delay

    def on_start(self):
        # Each burst user gets its own dedicated agent to avoid polluting the pool
        import httpx
        try:
            resp = httpx.post(
                f"{self.host}/v1/agents",
                json={
                    "name": f"burst-{uuid.uuid4().hex[:6]}",
                    "policy_group": "financial",
                    "tenant_id": "load_test",
                },
                headers={"X-Sentinel-Agent-Key": ADMIN_KEY, "Content-Type": "application/json"},
                timeout=10,
            )
            if resp.status_code == 201:
                self._api_key = resp.json()["api_key"]
                return
        except Exception:
            pass
        self._api_key = ADMIN_KEY

    @task
    def burst_web_fetch(self):
        with self.client.post(
            "/v1/tool-calls",
            json={"tool_name": "web_fetch", "arguments": {"url": "https://example.com/data"}},
            headers={
                "X-Sentinel-Agent-Key": self._api_key,
                "Content-Type": "application/json",
            },
            catch_response=True,
            name="[burst] web_fetch",
        ) as resp:
            # Both 200 (allowed) and 429 (rate limited) are expected outcomes
            if resp.status_code in (200, 429):
                resp.success()
            else:
                resp.failure(f"Unexpected {resp.status_code}")
