"""
JudgeCascade — orchestrates the two-tier judge system.

Tier 1: Llama-3.2-3B via Ollama (local, fast)
Tier 3: OpenAI API (fallback or forced path)

Escalation triggers:
  - Tier 1 asyncio.TimeoutError
  - Tier 1 confidence < settings.judge_tier1_confidence_threshold
  - Circuit breaker OPEN (skip Tier 1 entirely)
  - Any unhandled Tier 1 exception

On total failure (both tiers timeout/fail):
  - Returns HUMAN_REVIEW with reason "all_judges_timeout"
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog

from gateway.config import settings
from judge.circuit_breaker import CircuitOpenError, get_circuit_breaker
from judge.models import JudgeResult
from judge.tier1 import run_tier1
from judge.tier3 import run_tier3

log = structlog.get_logger()


async def run_cascade(
    agent_id: str,
    tool_name: str,
    arguments: dict[str, Any],
    task_description: str,
    conversation_history: list[dict],
    source_documents: list[dict],
    policy_group: str,
    policy_version: str,
    check_faithfulness: bool = True,
) -> JudgeResult:
    """
    Run the judge cascade. Always returns a JudgeResult — never raises.

    Returns HUMAN_REVIEW as a safe fallback if both tiers fail.
    """
    breaker = get_circuit_breaker()
    tier1_result: JudgeResult | None = None

    if not settings.judge_force_tier3_openai:
        # ------------------------------------------------------------------
        # Tier 1: Ollama (Llama-3.2-3B) — local, fast
        # ------------------------------------------------------------------
        try:
            async with breaker:
                tier1_result = await run_tier1(
                    agent_id=agent_id,
                    tool_name=tool_name,
                    arguments=arguments,
                    task_description=task_description,
                    conversation_history=conversation_history,
                    source_documents=source_documents,
                    policy_group=policy_group,
                    policy_version=policy_version,
                    check_faithfulness=check_faithfulness,
                    min_confidence=settings.judge_tier1_confidence_threshold,
                )
                log.info(
                    "tier1_judge_complete",
                    verdict=tier1_result.verdict,
                    confidence=tier1_result.confidence,
                    tool=tool_name,
                )
        except CircuitOpenError:
            log.warning("tier1_circuit_open", tool=tool_name)
        except asyncio.TimeoutError:
            log.warning("tier1_timeout", tool=tool_name, timeout=settings.ollama_timeout_seconds)
            await breaker.record_failure()
        except Exception as e:
            log.error("tier1_unexpected_error", tool=tool_name, error=str(e))
            await breaker.record_failure()

        # If Tier 1 succeeded and confidence is sufficient → done
        if tier1_result is not None and tier1_result.confidence >= settings.judge_tier1_confidence_threshold:
            return tier1_result

    # ------------------------------------------------------------------
    # Tier 3: OpenAI — fallback or forced path
    # ------------------------------------------------------------------
    if not settings.openai_api_key:
        # No API key configured — can't escalate
        if tier1_result is not None:
            return tier1_result   # Use low-confidence Tier 1 result
        return JudgeResult.human_review(
            reason="Tier 1 unavailable and Tier 3 not configured (no OPENAI_API_KEY).",
            tier=3,
            confidence=0.0,
        )

    try:
        tier3_result = await asyncio.wait_for(
            run_tier3(
                agent_id=agent_id,
                tool_name=tool_name,
                arguments=arguments,
                task_description=task_description,
                conversation_history=conversation_history,
                source_documents=source_documents,
                policy_group=policy_group,
                policy_version=policy_version,
                check_faithfulness=check_faithfulness,
            ),
            timeout=settings.openai_timeout_seconds,
        )
        log.info(
            "tier3_judge_complete",
            verdict=tier3_result.verdict,
            confidence=tier3_result.confidence,
            tool=tool_name,
        )
        return tier3_result

    except asyncio.TimeoutError:
        log.error("tier3_timeout", tool=tool_name, timeout=settings.openai_timeout_seconds)
    except Exception as e:
        log.error("tier3_unexpected_error", tool=tool_name, error=str(e))

    # ------------------------------------------------------------------
    # Both tiers failed — safe fallback
    # ------------------------------------------------------------------
    log.error("all_judges_failed", tool=tool_name)
    return JudgeResult.human_review(
        reason="All judge tiers failed or timed out. Routing to human review.",
        tier=3,
        confidence=0.0,
    )
