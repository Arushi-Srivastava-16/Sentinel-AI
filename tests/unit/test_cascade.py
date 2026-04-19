"""
Unit tests for JudgeCascade — mocks all LLM calls.
Tests escalation, circuit breaker bypass, timeout handling, and fallback.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from judge.models import (
    FaithfulnessResult,
    FaithfulnessVerdict,
    IntentCheckResult,
    JudgeResult,
    JudgeVerdict,
    ThreatLevel,
)


def _safe_intent() -> IntentCheckResult:
    return IntentCheckResult(threat_level=ThreatLevel.SAFE, confidence=0.92, explanation="Safe.")


def _malicious_intent() -> IntentCheckResult:
    return IntentCheckResult(
        threat_level=ThreatLevel.MALICIOUS,
        confidence=0.97,
        explanation="Prompt injection detected.",
        red_flags=["Contains 'ignore all rules'"],
    )


def _faithful() -> FaithfulnessResult:
    return FaithfulnessResult(verdict=FaithfulnessVerdict.FAITHFUL, confidence=0.95, explanation="Matches source.")


def _base_args():
    return {
        "agent_id": "agent_test",
        "tool_name": "execute_payment",
        "arguments": {"amount": 75000, "currency": "USD"},
        "task_description": "Pay Q1 invoice per contract",
        "conversation_history": [],
        "source_documents": [],
        "policy_group": "financial",
        "policy_version": "financial-1.0.0",
    }


class TestCascadeHappyPath:
    @pytest.mark.asyncio
    async def test_tier1_safe_returns_allowed(self):
        safe_result = JudgeResult.allowed("Safe intent, faithful to docs.", tier=1, confidence=0.92)

        with patch("judge.cascade.run_tier1", new_callable=AsyncMock, return_value=safe_result):
            with patch("judge.cascade.get_circuit_breaker") as mock_cb:
                # Circuit is closed (no-op context manager)
                mock_cb.return_value.__aenter__ = AsyncMock(return_value=None)
                mock_cb.return_value.__aexit__ = AsyncMock(return_value=False)
                mock_cb.return_value.record_failure = AsyncMock()

                from judge.cascade import run_cascade
                result = await run_cascade(**_base_args())

        assert result.verdict == JudgeVerdict.ALLOWED
        assert result.tier_used == 1

    @pytest.mark.asyncio
    async def test_tier1_blocked_returns_blocked(self):
        blocked_result = JudgeResult.blocked("Malicious intent.", tier=1, confidence=0.97)

        with patch("judge.cascade.run_tier1", new_callable=AsyncMock, return_value=blocked_result):
            with patch("judge.cascade.get_circuit_breaker") as mock_cb:
                mock_cb.return_value.__aenter__ = AsyncMock(return_value=None)
                mock_cb.return_value.__aexit__ = AsyncMock(return_value=False)
                mock_cb.return_value.record_failure = AsyncMock()

                from judge.cascade import run_cascade
                result = await run_cascade(**_base_args())

        assert result.verdict == JudgeVerdict.BLOCKED


class TestCircuitBreakerBypass:
    @pytest.mark.asyncio
    async def test_circuit_open_skips_tier1_and_goes_to_tier3(self):
        from judge.circuit_breaker import CircuitOpenError
        tier3_result = JudgeResult.allowed("Tier 3 safe.", tier=3, confidence=0.90)

        with patch("judge.cascade.get_circuit_breaker") as mock_cb:
            mock_cb.return_value.__aenter__ = AsyncMock(side_effect=CircuitOpenError("OPEN"))
            mock_cb.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_cb.return_value.record_failure = AsyncMock()

            with patch("judge.cascade.run_tier3", new_callable=AsyncMock, return_value=tier3_result):
                with patch("gateway.config.settings") as mock_settings:
                    mock_settings.openai_api_key = "sk-openai-test"
                    mock_settings.openai_timeout_seconds = 15
                    mock_settings.judge_force_tier3_openai = False
                    mock_settings.judge_tier1_confidence_threshold = 0.75
                    mock_settings.ollama_timeout_seconds = 3

                    from judge.cascade import run_cascade
                    result = await run_cascade(**_base_args())

        assert result.verdict == JudgeVerdict.ALLOWED
        assert result.tier_used == 3


class TestTimeoutHandling:
    @pytest.mark.asyncio
    async def test_tier1_timeout_escalates_to_tier3(self):
        tier3_result = JudgeResult.human_review("Uncertain.", tier=3, confidence=0.6)

        async def slow_tier1(*args, **kwargs):
            raise asyncio.TimeoutError()

        with patch("judge.cascade.run_tier1", new_callable=AsyncMock, side_effect=asyncio.TimeoutError()):
            with patch("judge.cascade.get_circuit_breaker") as mock_cb:
                mock_cb.return_value.__aenter__ = AsyncMock(return_value=None)
                mock_cb.return_value.__aexit__ = AsyncMock(side_effect=asyncio.TimeoutError())
                mock_cb.return_value.record_failure = AsyncMock()

                with patch("judge.cascade.run_tier3", new_callable=AsyncMock, return_value=tier3_result):
                    with patch("gateway.config.settings") as mock_settings:
                        mock_settings.openai_api_key = "sk-openai-test"
                        mock_settings.openai_timeout_seconds = 15
                        mock_settings.judge_force_tier3_openai = False
                        mock_settings.judge_tier1_confidence_threshold = 0.75
                        mock_settings.ollama_timeout_seconds = 3

                        from judge.cascade import run_cascade
                        result = await run_cascade(**_base_args())

        assert result.verdict == JudgeVerdict.HUMAN_REVIEW

    @pytest.mark.asyncio
    async def test_both_tiers_fail_returns_human_review(self):
        with patch("judge.cascade.run_tier1", new_callable=AsyncMock, side_effect=Exception("Tier1 dead")):
            with patch("judge.cascade.get_circuit_breaker") as mock_cb:
                mock_cb.return_value.__aenter__ = AsyncMock(return_value=None)
                mock_cb.return_value.__aexit__ = AsyncMock(side_effect=Exception("Tier1 dead"))
                mock_cb.return_value.record_failure = AsyncMock()

                with patch("judge.cascade.run_tier3", new_callable=AsyncMock, side_effect=Exception("Tier3 dead")):
                    with patch("gateway.config.settings") as mock_settings:
                        mock_settings.openai_api_key = "sk-openai-test"
                        mock_settings.openai_timeout_seconds = 15
                        mock_settings.judge_force_tier3_openai = False
                        mock_settings.judge_tier1_confidence_threshold = 0.75
                        mock_settings.ollama_timeout_seconds = 3

                        from judge.cascade import run_cascade
                        result = await run_cascade(**_base_args())

        assert result.verdict == JudgeVerdict.HUMAN_REVIEW
        assert "failed" in result.reason.lower() or "timeout" in result.reason.lower()

    @pytest.mark.asyncio
    async def test_no_openai_key_falls_back_gracefully(self):
        tier1_result = JudgeResult.human_review("Low confidence", tier=1, confidence=0.5)

        with patch("judge.cascade.run_tier1", new_callable=AsyncMock, return_value=tier1_result):
            with patch("judge.cascade.get_circuit_breaker") as mock_cb:
                mock_cb.return_value.__aenter__ = AsyncMock(return_value=None)
                mock_cb.return_value.__aexit__ = AsyncMock(return_value=False)
                mock_cb.return_value.record_failure = AsyncMock()

                with patch("gateway.config.settings") as mock_settings:
                    mock_settings.openai_api_key = ""   # no key
                    mock_settings.judge_force_tier3_openai = False
                    mock_settings.judge_tier1_confidence_threshold = 0.75
                    mock_settings.ollama_timeout_seconds = 3

                    from judge.cascade import run_cascade
                    result = await run_cascade(**_base_args())

        # Should return tier1's low-confidence result rather than crash
        assert result.verdict == JudgeVerdict.HUMAN_REVIEW
