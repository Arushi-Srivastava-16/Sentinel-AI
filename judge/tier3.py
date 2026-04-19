"""
Judge Tier 3 — OpenAI API.

Used as fallback when:
  - Tier 1 confidence is below threshold
  - Tier 1 times out (circuit breaker OPEN)
  - Request is escalated as high-stakes

Times out at settings.openai_timeout_seconds (default 20s).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import instructor
from openai import AsyncOpenAI
from jinja2 import Environment, FileSystemLoader, select_autoescape

from gateway.config import settings
from judge.models import (
    FaithfulnessResult,
    FaithfulnessVerdict,
    IntentCheckResult,
    JudgeResult,
    JudgeVerdict,
    ThreatLevel,
)

_PROMPTS_DIR = __file__.replace("tier3.py", "prompts")
_jinja = Environment(
    loader=FileSystemLoader(_PROMPTS_DIR),
    autoescape=select_autoescape([]),
)
_jinja.filters["tojson"] = json.dumps


def _get_openai_instructor_client() -> instructor.AsyncInstructor:
    raw = AsyncOpenAI(
        api_key=settings.openai_api_key,
        timeout=settings.openai_timeout_seconds,
        base_url=settings.openai_base_url,
    )
    return instructor.from_openai(raw, mode=instructor.Mode.JSON)


async def run_tier3(
    agent_id: str,
    tool_name: str,
    arguments: dict[str, Any],
    task_description: str,
    conversation_history: list[dict],
    source_documents: list[dict],
    policy_group: str,
    policy_version: str,
    check_faithfulness: bool = True,
    min_confidence: float = 0.80,
) -> JudgeResult:
    """
    Run Tier 3 judge (OpenAI). Never escalates — always returns a final verdict.
    Raises asyncio.TimeoutError if API doesn't respond.
    """
    from judge.tier1 import _render_faithfulness_prompt, _render_intent_prompt, _verdict_from_results

    timeout = settings.openai_timeout_seconds
    client = _get_openai_instructor_client()

    # Intent check
    intent_prompt = _render_intent_prompt(
        agent_id=agent_id,
        tool_name=tool_name,
        arguments=arguments,
        task_description=task_description,
        conversation_history=conversation_history,
        policy_group=policy_group,
        policy_version=policy_version,
    )
    intent_result: IntentCheckResult = await asyncio.wait_for(
        client.chat.completions.create(
            model=settings.openai_model,
            messages=[{"role": "user", "content": intent_prompt}],
            response_model=IntentCheckResult,
        ),
        timeout=timeout,
    )

    faithfulness_result: FaithfulnessResult | None = None
    if check_faithfulness:
        faith_prompt = _render_faithfulness_prompt(
            tool_name=tool_name,
            arguments=arguments,
            task_description=task_description,
            source_documents=source_documents,
        )
        faithfulness_result = await asyncio.wait_for(
            client.chat.completions.create(
                model=settings.openai_model,
                messages=[{"role": "user", "content": faith_prompt}],
                response_model=FaithfulnessResult,
            ),
            timeout=timeout,
        )

    result = _verdict_from_results(intent_result, faithfulness_result, min_confidence)
    # Tier 3 never returns HUMAN_REVIEW with confidence < 0.5 — it must commit
    if result.verdict == JudgeVerdict.HUMAN_REVIEW and result.confidence < 0.5:
        # Force a decision — Tier 3 is the last resort
        return JudgeResult.human_review(
            reason=f"Tier 3 uncertain: {result.reason}",
            tier=3,
            confidence=0.5,
        )
    # Override tier number to 3
    result.tier_used = 3
    return result
