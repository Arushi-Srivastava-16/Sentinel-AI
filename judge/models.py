"""
Pydantic models for structured LLM output (used with `instructor`).
These are the expected JSON schemas the judge must return.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


# =============================================================================
# Intent Check output
# =============================================================================

class ThreatLevel(str, Enum):
    SAFE       = "safe"
    SUSPICIOUS = "suspicious"
    MALICIOUS  = "malicious"


class IntentCheckResult(BaseModel):
    threat_level: ThreatLevel
    confidence: float = Field(..., ge=0.0, le=1.0)
    explanation: str = Field(..., max_length=500)
    red_flags: list[str] = Field(default_factory=list)


# =============================================================================
# Faithfulness Check output
# =============================================================================

class FaithfulnessVerdict(str, Enum):
    FAITHFUL   = "faithful"
    UNFAITHFUL = "unfaithful"
    UNCERTAIN  = "uncertain"


class FaithfulnessResult(BaseModel):
    verdict: FaithfulnessVerdict
    confidence: float = Field(..., ge=0.0, le=1.0)
    explanation: str = Field(..., max_length=500)
    discrepancies: list[str] = Field(default_factory=list)


# =============================================================================
# Combined Judge result
# =============================================================================

class JudgeVerdict(str, Enum):
    ALLOWED      = "ALLOWED"
    BLOCKED      = "BLOCKED"
    HUMAN_REVIEW = "HUMAN_REVIEW"


class JudgeResult(BaseModel):
    verdict: JudgeVerdict
    confidence: float = Field(..., ge=0.0, le=1.0)
    reason: str
    tier_used: int                       # 1 = Ollama/Llama, 3 = Claude Haiku
    intent_result: IntentCheckResult | None = None
    faithfulness_result: FaithfulnessResult | None = None

    @classmethod
    def blocked(cls, reason: str, tier: int, confidence: float = 1.0) -> "JudgeResult":
        return cls(verdict=JudgeVerdict.BLOCKED, confidence=confidence, reason=reason, tier_used=tier)

    @classmethod
    def allowed(cls, reason: str, tier: int, confidence: float = 1.0) -> "JudgeResult":
        return cls(verdict=JudgeVerdict.ALLOWED, confidence=confidence, reason=reason, tier_used=tier)

    @classmethod
    def human_review(cls, reason: str, tier: int, confidence: float = 0.5) -> "JudgeResult":
        return cls(verdict=JudgeVerdict.HUMAN_REVIEW, confidence=confidence, reason=reason, tier_used=tier)
