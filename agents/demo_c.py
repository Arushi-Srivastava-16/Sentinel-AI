"""
Demo Scenario C — "The Policy Version Rollback"
================================================
A code executor agent that runs across a live policy version change.

Sequence:
  1. Confirm financial policy v1.0.0 is active
  2. Send execute_code() → ALLOWED (v1 permits it)
  3. Admin activates policy v2.0.0 via gateway API
  4. Send identical execute_code() → HUMAN_REVIEW (v2 requires review)
  5. Query Neo4j: "which v1-allowed calls would v2 block?"

This demonstrates:
  - Live policy activation without gateway restart
  - Per-decision policy version recorded in Neo4j
  - Compliance query: policy diff across versions

Run:
    python agents/demo_c.py
    # or
    make demo-c

Requires:
  - Sentinel gateway running
  - policies/examples/financial-v1.yaml and financial-v2.yaml loaded
  - SENTINEL_ADMIN_KEY env var for policy activation
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent.parent))

from sentinel_sdk import AgentClient, Verdict

GATEWAY_URL    = os.getenv("SENTINEL_GATEWAY_URL", "http://localhost:8000")
AGENT_API_KEY  = os.getenv("DEMO_C_API_KEY", "snl_REPLACE_WITH_REAL_KEY")
AGENT_ID       = os.getenv("DEMO_C_AGENT_ID", "agent_demo_c")
ADMIN_API_KEY  = os.getenv("SENTINEL_ADMIN_KEY", "snl_admin_changeme")

GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

CODE_CALL = {
    "tool_name": "execute_code",
    "arguments": {
        "language": "python",
        "code": "import pandas as pd; df = pd.read_csv('report.csv'); print(df.describe())",
    },
    "context": {
        "task_description": "Run data analysis on Q1 report CSV"
    },
}


async def activate_policy(version: str) -> None:
    """Call the admin endpoint to activate a policy version."""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{GATEWAY_URL}/v1/policies/financial/activate",
            json={"version": version},
            headers={"X-Sentinel-Agent-Key": ADMIN_API_KEY},
            timeout=10.0,
        )
        response.raise_for_status()
        data = response.json()
        print(f"  Policy activated: financial v{data.get('version')} "
              f"(effective from {data.get('effective_from')})")


async def run() -> None:
    print(f"\n{BOLD}{CYAN}=" * 60 + RESET)
    print(f"{BOLD}{CYAN}  Sentinel Demo C — Policy Version Rollback{RESET}")
    print(f"{BOLD}{CYAN}=" * 60 + RESET + "\n")

    async with AgentClient(
        gateway_url=GATEWAY_URL,
        api_key=AGENT_API_KEY,
        agent_id=AGENT_ID,
        session_id="demo_c_session",
    ) as sentinel:

        # ------------------------------------------------------------------ #
        # Step 1: Ensure v1 is active
        # ------------------------------------------------------------------ #
        print(f"{BOLD}Step 1: Activate financial policy v1.0.0{RESET}")
        await activate_policy("1.0.0")
        print()

        # ------------------------------------------------------------------ #
        # Step 2: execute_code under v1 → should be ALLOWED
        # ------------------------------------------------------------------ #
        print(f"{BOLD}Step 2: execute_code under v1 → expect ALLOWED{RESET}")
        print(f"  Tool: {CODE_CALL['tool_name']}")
        print(f"  Args: {CODE_CALL['arguments']}")

        d1 = await sentinel.check(**CODE_CALL)  # type: ignore[arg-type]
        colour = GREEN if d1.verdict == Verdict.ALLOWED else RED
        status = "PASS" if d1.verdict == Verdict.ALLOWED else "FAIL"
        print(
            f"  Got:  {colour}{d1.verdict.value}{RESET} "
            f"via {d1.path.value} ({d1.latency_ms:.1f}ms) "
            f"[policy: {d1.policy_version}] [{GREEN if status == 'PASS' else RED}{status}{RESET}]"
        )
        print()

        # Pause to make the timeline visible in the dashboard
        print(f"{CYAN}Pausing 2s to make policy change visible on dashboard...{RESET}\n")
        await asyncio.sleep(2)

        # ------------------------------------------------------------------ #
        # Step 3: Admin activates v2 (BREAKING — adds human_review for execute_code)
        # ------------------------------------------------------------------ #
        print(f"{BOLD}Step 3: Admin activates financial policy v2.0.0{RESET}")
        print(f"  {YELLOW}BREAKING CHANGE: execute_code now requires HUMAN_REVIEW{RESET}")
        await activate_policy("2.0.0")
        print()

        # ------------------------------------------------------------------ #
        # Step 4: Same execute_code call under v2 → should be HUMAN_REVIEW
        # ------------------------------------------------------------------ #
        print(f"{BOLD}Step 4: Same execute_code call under v2 → expect HUMAN_REVIEW{RESET}")

        d2 = await sentinel.check(**CODE_CALL)  # type: ignore[arg-type]
        colour = YELLOW if d2.verdict == Verdict.HUMAN_REVIEW else RED
        status = "PASS" if d2.verdict == Verdict.HUMAN_REVIEW else "FAIL"
        print(
            f"  Got:  {colour}{d2.verdict.value}{RESET} "
            f"via {d2.path.value} ({d2.latency_ms:.1f}ms) "
            f"[policy: {d2.policy_version}] [{GREEN if status == 'PASS' else RED}{status}{RESET}]"
        )
        print()

        # ------------------------------------------------------------------ #
        # Step 5: Print the Neo4j compliance query (run manually or via dashboard)
        # ------------------------------------------------------------------ #
        print(f"{BOLD}Step 5: Compliance query — decisions allowed under v1 blocked by v2{RESET}")
        print(f"""
  {CYAN}Run this in Neo4j Browser (localhost:7474):{RESET}

  MATCH (tc:ToolCall)-[:RESULTED_IN]->(d:Decision)
        -[:EVALUATED_UNDER]->(pv:PolicyVersion {{version: "1.0.0"}})
  WHERE d.verdict = "ALLOWED"
    AND tc.tool_name = "execute_code"
  RETURN tc.id, tc.tool_name, d.decision_id, d.verdict,
         pv.version AS evaluated_under
  ORDER BY tc.timestamp_ns DESC
  LIMIT 20;
""")

        # Results summary
        v1_ok = d1.verdict == Verdict.ALLOWED
        v2_ok = d2.verdict == Verdict.HUMAN_REVIEW
        all_pass = v1_ok and v2_ok

        print(f"{BOLD}{'=' * 60}{RESET}")
        print(f"{BOLD}Results:{RESET}")
        print(f"  v1 execute_code → ALLOWED:       {GREEN+'PASS'+RESET if v1_ok else RED+'FAIL'+RESET}")
        print(f"  v2 execute_code → HUMAN_REVIEW:  {GREEN+'PASS'+RESET if v2_ok else RED+'FAIL'+RESET}")
        print(f"\n{BOLD}{'Policy rollback demo: ' + (GREEN+'PASS'+RESET if all_pass else RED+'FAIL'+RESET)}{RESET}\n")

        sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    asyncio.run(run())
