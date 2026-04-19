"""
Demo Scenario B — "The Rate Limit Abuser"
==========================================
A web scraper agent that sends 200 tool calls in rapid succession.

Expected behaviour:
  - Calls 1–50:   ALLOWED  (within token bucket of 50)
  - Calls 51–200: BLOCKED  with HTTP 429 (rate limit exceeded)
  - Other agents are unaffected (agent isolation)

Run:
    python agents/demo_b.py
    # or
    make demo-b

Requires: Sentinel gateway running at SENTINEL_GATEWAY_URL
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sentinel_sdk import AgentClient, Verdict

GATEWAY_URL = os.getenv("SENTINEL_GATEWAY_URL", "http://localhost:8000")
AGENT_API_KEY = os.getenv("DEMO_B_API_KEY", "snl_REPLACE_WITH_REAL_KEY")
AGENT_ID = os.getenv("DEMO_B_AGENT_ID", "agent_demo_b")

TOTAL_REQUESTS = 200
RATE_LIMIT_TOKENS = 50   # must match policy for this agent

GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"


async def run() -> None:
    print(f"\n{BOLD}{CYAN}=" * 60 + RESET)
    print(f"{BOLD}{CYAN}  Sentinel Demo B — The Rate Limit Abuser{RESET}")
    print(f"{BOLD}{CYAN}=" * 60 + RESET + "\n")
    print(f"Sending {TOTAL_REQUESTS} web_fetch calls as fast as possible.")
    print(f"Expected: first {RATE_LIMIT_TOKENS} ALLOWED, rest BLOCKED (429)\n")

    allowed_count = 0
    blocked_count = 0
    error_count = 0
    latencies: list[float] = []

    async with AgentClient(
        gateway_url=GATEWAY_URL,
        api_key=AGENT_API_KEY,
        agent_id=AGENT_ID,
        session_id="demo_b_session",
    ) as sentinel:

        start = time.monotonic()

        for i in range(1, TOTAL_REQUESTS + 1):
            try:
                decision = await sentinel.check(
                    tool_name="web_fetch",
                    arguments={"url": f"https://example.com/page/{i}"},
                )
                latencies.append(decision.latency_ms)

                if decision.verdict == Verdict.ALLOWED:
                    allowed_count += 1
                    marker = f"{GREEN}✓{RESET}"
                else:
                    blocked_count += 1
                    marker = f"{RED}✗{RESET}"

                # Print inline progress bar (every 10 requests)
                if i % 10 == 0 or i <= 5:
                    elapsed = time.monotonic() - start
                    rps = i / elapsed if elapsed > 0 else 0
                    print(
                        f"  [{i:3d}/{TOTAL_REQUESTS}] {marker} "
                        f"allowed={GREEN}{allowed_count}{RESET} "
                        f"blocked={RED}{blocked_count}{RESET} "
                        f"rps={rps:.0f}"
                    )

            except Exception as e:
                error_count += 1
                print(f"  [{i:3d}] {RED}Error: {e}{RESET}")

        elapsed = time.monotonic() - start

    # Results
    avg_latency = sum(latencies) / len(latencies) if latencies else 0
    p95_latency = sorted(latencies)[int(len(latencies) * 0.95)] if latencies else 0

    expected_allowed = RATE_LIMIT_TOKENS
    expected_blocked = TOTAL_REQUESTS - RATE_LIMIT_TOKENS
    verdict_correct = (
        allowed_count == expected_allowed and blocked_count == expected_blocked
    )

    print(f"\n{BOLD}{'=' * 60}{RESET}")
    print(f"{BOLD}Summary:{RESET}")
    print(f"  Total requests:   {TOTAL_REQUESTS}")
    print(f"  Allowed:          {GREEN}{allowed_count}{RESET} (expected {expected_allowed})")
    print(f"  Blocked:          {RED}{blocked_count}{RESET} (expected {expected_blocked})")
    print(f"  Errors:           {error_count}")
    print(f"  Elapsed:          {elapsed:.2f}s ({TOTAL_REQUESTS/elapsed:.0f} req/s)")
    print(f"  Avg latency:      {avg_latency:.1f}ms")
    print(f"  p95 latency:      {p95_latency:.1f}ms")

    colour = GREEN if verdict_correct else RED
    status = "PASS" if verdict_correct else "FAIL"
    print(f"\n{BOLD}{colour}Rate limit enforcement: {status}{RESET}\n")

    sys.exit(0 if verdict_correct else 1)


if __name__ == "__main__":
    asyncio.run(run())
