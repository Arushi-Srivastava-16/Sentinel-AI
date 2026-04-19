"""
E2E test configuration — runs against a live Docker Compose stack.

Prerequisites:
    docker-compose up -d
    Wait for health checks to pass (make dev-up handles this)

Environment variables read from host env or .env file:
    SENTINEL_GATEWAY_URL  (default: http://localhost:8000)
    SENTINEL_ADMIN_KEY    (must match SENTINEL_ADMIN_API_KEY in .env)
"""

from __future__ import annotations

import os
import time

import httpx
import pytest

GATEWAY_URL = os.getenv("SENTINEL_GATEWAY_URL", "http://localhost:8000")
ADMIN_KEY = os.getenv("SENTINEL_ADMIN_API_KEY", "snl_admin_dev_key_replace_in_production")


def wait_for_gateway(max_seconds: int = 60) -> None:
    deadline = time.monotonic() + max_seconds
    while time.monotonic() < deadline:
        try:
            r = httpx.get(f"{GATEWAY_URL}/health", timeout=3)
            if r.status_code == 200:
                return
        except httpx.TransportError:
            pass
        time.sleep(2)
    pytest.skip(f"Gateway not reachable at {GATEWAY_URL} after {max_seconds}s — skipping E2E suite")


@pytest.fixture(scope="session", autouse=True)
def gateway_healthy():
    wait_for_gateway()


@pytest.fixture(scope="session")
def admin_headers() -> dict:
    return {"X-Sentinel-Agent-Key": ADMIN_KEY, "Content-Type": "application/json"}


@pytest.fixture(scope="session")
def gateway_url() -> str:
    return GATEWAY_URL


def register_agent(gateway_url: str, admin_headers: dict, name: str, policy_group: str = "financial") -> tuple[str, str]:
    """Register a test agent and return (agent_id, api_key)."""
    resp = httpx.post(
        f"{gateway_url}/v1/agents",
        json={"name": name, "policy_group": policy_group, "tenant_id": "test_tenant"},
        headers=admin_headers,
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["agent_id"], data["api_key"]
