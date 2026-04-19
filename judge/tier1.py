"""
Judge Tier 1 — Llama-3.2-3B via Ollama (local inference).

Runs intent check and optionally faithfulness check.
Times out at settings.ollama_timeout_seconds (default 3s).
Returns JudgeResult. Raises asyncio.TimeoutError on timeout.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from gateway.config import settings
from judge.client import ollama_chat
from judge.models import (
    FaithfulnessResult,
    FaithfulnessVerdict,
    IntentCheckResult,
    JudgeResult,
    ThreatLevel,
)

_PROMPTS_DIR = __file__.replace("tier1.py", "prompts")
_jinja = Environment(
    loader=FileSystemLoader(_PROMPTS_DIR),
    autoescape=select_autoescape([]),
)
_jinja.filters["tojson"] = json.dumps


def _render_intent_prompt(
    agent_id: str,
    tool_name: str,
    arguments: dict[str, Any],
    task_description: str,
    conversation_history: list[dict],
    policy_group: str,
    policy_version: str,
) -> str:
    tmpl = _jinja.get_template("intent_check.j2")
    return tmpl.render(
        agent_id=agent_id,
        tool_name=tool_name,
        arguments=arguments,
        task_description=task_description,
        conversation_history=conversation_history,
        policy_group=policy_group,
        policy_version=policy_version,
    )


def _render_faithfulness_prompt(
    tool_name: str,
    arguments: dict[str, Any],
    task_description: str,
    source_documents: list[dict],
) -> str:
    tmpl = _jinja.get_template("faithfulness.j2")
    return tmpl.render(
        tool_name=tool_name,
        arguments=arguments,
        task_description=task_description,
        source_documents=source_documents,
    )


def _verdict_from_results(
    intent: IntentCheckResult,
    faithfulness: FaithfulnessResult | None,
    min_confidence: float,
) -> JudgeResult:
    """Map intent + faithfulness checks to a final JudgeResult."""

    # Immediate block conditions
    if intent.threat_level == ThreatLevel.MALICIOUS:
        return JudgeResult.blocked(
            reason=f"Malicious intent detected: {intent.explanation}",
            tier=1,
            confidence=intent.confidence,
        )

    if faithfulness and faithfulness.verdict == FaithfulnessVerdict.UNFAITHFUL:
        return JudgeResult.blocked(
            reason=f"Action is unfaithful to source documents: {faithfulness.explanation}",
            tier=1,
            confidence=faithfulness.confidence,
        )

    # Human review conditions
    if intent.threat_level == ThreatLevel.SUSPICIOUS:
        return JudgeResult.human_review(
            reason=f"Suspicious intent: {intent.explanation}",
            tier=1,
            confidence=intent.confidence,
        )

    if faithfulness and faithfulness.verdict == FaithfulnessVerdict.UNCERTAIN:
        return JudgeResult.human_review(
            reason=f"Cannot verify faithfulness: {faithfulness.explanation}",
            tier=1,
            confidence=faithfulness.confidence,
        )

    # Low confidence → escalate
    combined_confidence = intent.confidence
    if faithfulness:
        combined_confidence = min(intent.confidence, faithfulness.confidence)

    if combined_confidence < min_confidence:
        return JudgeResult.human_review(
            reason=f"Confidence too low ({combined_confidence:.2f} < {min_confidence}): {intent.explanation}",
            tier=1,
            confidence=combined_confidence,
        )

    # All clear
    return JudgeResult(
        verdict=__import__("judge.models", fromlist=["JudgeVerdict"]).JudgeVerdict.ALLOWED,
        confidence=combined_confidence,
        reason=f"Safe: {intent.explanation}",
        tier_used=1,
        intent_result=intent,
        faithfulness_result=faithfulness,
    )


async def run_tier1(
    agent_id: str,
    tool_name: str,
    arguments: dict[str, Any],
    task_description: str,
    conversation_history: list[dict],
    source_documents: list[dict],
    policy_group: str,
    policy_version: str,
    check_faithfulness: bool = True,
    min_confidence: float = 0.75,
) -> JudgeResult:
    """
    Run Tier 1 judge (Llama-3.2-3B).
    Raises asyncio.TimeoutError if Ollama doesn't respond in time.
    """
    timeout = settings.ollama_timeout_seconds

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
        ollama_chat(intent_prompt, IntentCheckResult),
        timeout=timeout,
    )

    # Faithfulness check (optional, only when source docs available)
    faithfulness_result: FaithfulnessResult | None = None
    if check_faithfulness:
        faith_prompt = _render_faithfulness_prompt(
            tool_name=tool_name,
            arguments=arguments,
            task_description=task_description,
            source_documents=source_documents,
        )
        faithfulness_result = await asyncio.wait_for(
            ollama_chat(faith_prompt, FaithfulnessResult),
            timeout=timeout,
        )

    return _verdict_from_results(intent_result, faithfulness_result, min_confidence)
