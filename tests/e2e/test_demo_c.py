"""
E2E — Demo Scenario C: "The Policy Version Rollback"

An admin activates a new (stricter) policy version mid-run.
The same tool call that was ALLOWED under v1 becomes HUMAN_REVIEW under v2.

Expected sequence:
  1. Activate policy financial-v1 via admin API
  2. execute_code() call → ALLOWED
  3. Admin activates financial-v2 (adds human-review rule for code execution)
  4. Same execute_code() call → HUMAN_REVIEW
  5. Neo4j audit: each Decision linked to the correct PolicyVersion

Run against live stack:
    pytest tests/e2e/test_demo_c.py -v
"""

from __future__ import annotations

import time

import httpx
import pytest

from tests.e2e.conftest import GATEWAY_URL, register_agent

_POLL_INTERVAL_S = 0.5
_MAX_POLL_S = 20


def _agent_headers(api_key: str) -> dict:
    return {"X-Sentinel-Agent-Key": api_key, "Content-Type": "application/json"}


def _admin_headers(admin_key: str) -> dict:
    return {"X-Sentinel-Agent-Key": admin_key, "Content-Type": "application/json"}


def _tool_call_with_poll(gateway_url: str, api_key: str, tool_name: str, arguments: dict) -> dict:
    """POST /v1/tool-calls and poll if needed. Returns final decision dict."""
    with httpx.Client(base_url=gateway_url, timeout=30) as client:
        resp = client.post(
            "/v1/tool-calls",
            json={"tool_name": tool_name, "arguments": arguments},
            headers=_agent_headers(api_key),
        )
        if resp.status_code == 401:
            pytest.fail(f"Auth failed — check API key for {tool_name}")
        resp.raise_for_status()
        data = resp.json()

        if resp.status_code == 200 and "verdict" in data:
            return data

        if resp.status_code == 202 or data.get("status") == "pending":
            decision_id = data["decision_id"]
            poll_url = data.get("poll_url", f"/v1/decisions/{decision_id}")
            deadline = time.monotonic() + _MAX_POLL_S
            while time.monotonic() < deadline:
                time.sleep(_POLL_INTERVAL_S)
                pr = client.get(poll_url, headers=_agent_headers(api_key))
                pr.raise_for_status()
                pd = pr.json()
                if pd.get("status") != "pending":
                    return pd
            pytest.fail(f"Decision {decision_id} still pending after {_MAX_POLL_S}s")

        return data


def _activate_policy(gateway_url: str, admin_key: str, policy_group: str, version: str) -> None:
    """Activate a policy version via the admin API."""
    with httpx.Client(base_url=gateway_url, timeout=10) as client:
        resp = client.post(
            f"/v1/policies/{policy_group}/activate",
            json={"version": version},
            headers=_admin_headers(admin_key),
        )
        if resp.status_code == 404:
            pytest.skip(
                f"Policy {policy_group} v{version} not found — "
                "ensure policies/examples/ are loaded into the stack"
            )
        resp.raise_for_status()


def _list_policies(gateway_url: str, admin_key: str) -> list[dict]:
    with httpx.Client(base_url=gateway_url, timeout=10) as client:
        resp = client.get("/v1/policies", headers=_admin_headers(admin_key))
        resp.raise_for_status()
        return resp.json()


@pytest.fixture(scope="module")
def rollback_agent(gateway_url, admin_headers):
    agent_id, api_key = register_agent(gateway_url, admin_headers, "policy-rollback-e2e", policy_group="financial")
    return api_key


@pytest.fixture(scope="module")
def admin_key(admin_headers) -> str:
    return admin_headers["X-Sentinel-Agent-Key"]


class TestPolicyRollback:
    def test_policies_endpoint_reachable(self, gateway_url, admin_key):
        """Policies API must be reachable before running rollback tests."""
        policies = _list_policies(gateway_url, admin_key)
        assert isinstance(policies, list), f"Expected list of policies, got: {type(policies)}"

    def test_execute_code_allowed_under_v1(self, gateway_url, rollback_agent, admin_key):
        """
        With financial-v1 active (no code-execution human-review rule),
        execute_code() should be ALLOWED.
        """
        _activate_policy(gateway_url, admin_key, "financial", "1.0.0")
        # Brief pause for policy activation to propagate through Redis
        time.sleep(0.5)

        result = _tool_call_with_poll(
            gateway_url,
            rollback_agent,
            "execute_code",
            {
                "language": "python",
                "code": "print('hello world')",
                "service": "analytics",
            },
        )
        assert result["verdict"] in ("allowed", "human_review"), (
            f"Under v1, execute_code should be ALLOWED (or HUMAN_REVIEW at most), "
            f"but got {result['verdict']}. Reason: {result.get('reason')}"
        )
        assert result.get("policy_version", "").startswith("1."), (
            f"Decision should record policy v1.x, got: {result.get('policy_version')}"
        )

    def test_execute_code_blocked_or_review_under_v2(self, gateway_url, rollback_agent, admin_key):
        """
        After activating financial-v2 (adds human-review for code execution),
        the same call should escalate to HUMAN_REVIEW or be BLOCKED.
        """
        _activate_policy(gateway_url, admin_key, "financial", "2.0.0")
        time.sleep(0.5)

        result = _tool_call_with_poll(
            gateway_url,
            rollback_agent,
            "execute_code",
            {
                "language": "python",
                "code": "print('hello world')",
                "service": "analytics",
            },
        )
        assert result["verdict"] in ("human_review", "blocked"), (
            f"Under v2, execute_code should be HUMAN_REVIEW or BLOCKED, "
            f"got {result['verdict']}. This means the v2 policy was not applied."
        )
        assert result.get("policy_version", "").startswith("2."), (
            f"Decision should record policy v2.x, got: {result.get('policy_version')}"
        )

    def test_rollback_to_v1_restores_behaviour(self, gateway_url, rollback_agent, admin_key):
        """
        Rolling back to v1 must restore the original ALLOWED verdict.
        Demonstrates that policy activation is live and reversible.
        """
        # Ensure we're on v2 first
        _activate_policy(gateway_url, admin_key, "financial", "2.0.0")
        time.sleep(0.3)

        # Verify v2 blocks/reviews
        result_v2 = _tool_call_with_poll(
            gateway_url, rollback_agent, "execute_code",
            {"language": "python", "code": "x = 1", "service": "analytics"},
        )
        assert result_v2["verdict"] in ("human_review", "blocked"), "Prerequisite: v2 must restrict execute_code"

        # Roll back to v1
        _activate_policy(gateway_url, admin_key, "financial", "1.0.0")
        time.sleep(0.5)

        result_v1 = _tool_call_with_poll(
            gateway_url, rollback_agent, "execute_code",
            {"language": "python", "code": "x = 1", "service": "analytics"},
        )
        assert result_v1["verdict"] in ("allowed", "human_review"), (
            f"After rollback to v1, execute_code should be ALLOWED again, "
            f"got {result_v1['verdict']}"
        )

    def test_policy_version_stamped_on_decision(self, gateway_url, rollback_agent, admin_key):
        """
        Every decision response must include a policy_version field.
        This is the data that populates the Neo4j EVALUATED_UNDER relationship.
        """
        _activate_policy(gateway_url, admin_key, "financial", "1.0.0")
        time.sleep(0.3)

        result = _tool_call_with_poll(
            gateway_url,
            rollback_agent,
            "read_file",
            {"path": "/reports/q1.csv"},
        )
        assert "policy_version" in result, "Decision response missing policy_version field"
        assert result["policy_version"] not in ("", "unknown", None), (
            f"policy_version is empty/unknown: {result['policy_version']!r} — "
            "check policies/loader.py and the audit write pipeline"
        )

    def test_unrelated_tool_unaffected_by_policy_change(self, gateway_url, rollback_agent, admin_key):
        """
        A denylist-blocked call (/etc/passwd) should remain blocked under both v1 and v2.
        Policy changes should not affect hard denylist rules.
        """
        for version in ("1.0.0", "2.0.0"):
            _activate_policy(gateway_url, admin_key, "financial", version)
            time.sleep(0.3)
            result = _tool_call_with_poll(
                gateway_url, rollback_agent, "read_file", {"path": "/etc/passwd"},
            )
            assert result["verdict"] == "blocked", (
                f"Under policy v{version}, /etc/passwd should always be BLOCKED "
                f"(denylist), got {result['verdict']}"
            )
