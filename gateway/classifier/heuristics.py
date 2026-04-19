"""
Risk classification heuristics — decide whether a tool call needs
the cognitive (LLM) path or can be handled by fast path alone.

The classifier runs BEFORE policy evaluation. It answers:
  "Does this request need a human-quality judgment, or are rules enough?"

Fast path:  deterministic, <10ms
Cognitive:  LLM judge, 200-500ms

Classification logic (first match wins):
  1. Tool in LOW_RISK_TOOLS AND no suspicious keywords → fast_path
  2. Tool in HIGH_RISK_TOOLS                           → cognitive_path
  3. Arguments contain PII indicators                  → cognitive_path
  4. Context contains suspicious language              → cognitive_path
  5. Payment above cognitive threshold                 → cognitive_path
  6. Default                                           → fast_path (policy rules decide)
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any

from gateway.config import settings


class PathDecision(str, Enum):
    FAST      = "fast_path"
    COGNITIVE = "cognitive_path"


# Tools that are inherently low risk — never need LLM judgment
LOW_RISK_TOOLS: frozenset[str] = frozenset({
    "read_file",
    "list_directory",
    "web_fetch",
    "get_time",
    "ping",
    "echo",
    "get_config",
    "check_status",
    "query_metrics",
})

# Tools that always need LLM judgment regardless of arguments
HIGH_RISK_TOOLS: frozenset[str] = frozenset({
    "execute_payment",
    "wire_transfer",
    "send_email",
    "send_slack",
    "modify_permissions",
    "delete_record",
    "drop_table",
    "execute_shell",
    "deploy_code",
    "modify_iam",
})

# Argument keys that signal PII — always escalate
_PII_ARGUMENT_KEYS: frozenset[str] = frozenset({
    "ssn", "social_security", "credit_card", "card_number",
    "date_of_birth", "dob", "passport", "tax_id", "medical_record",
    "patient_id", "health_data",
})

# Regex patterns in reasoning/context that signal suspicious intent
_SUSPICIOUS_PATTERNS: list[re.Pattern] = [
    re.compile(r"ignore\s+(all\s+)?(previous\s+)?(rules|instructions|constraints)", re.I),
    re.compile(r"disregard\s+(all\s+)?", re.I),
    re.compile(r"jailbreak|DAN\s+mode|pretend\s+you\s+are", re.I),
    re.compile(r"bypass\s+(security|policy|filter|check)", re.I),
    re.compile(r"as\s+(root|admin|superuser)", re.I),
]

# Payment amount above which we always apply cognitive check
_PAYMENT_COGNITIVE_THRESHOLD: float = 10_000.0


def classify(
    tool_name: str,
    arguments: dict[str, Any],
    context: dict[str, Any],
) -> PathDecision:
    """
    Classify a tool call as fast_path or cognitive_path.
    This runs before any policy rules — it's a pre-filter.
    """
    # 0. Global override — route every tool call to cognitive path.
    if settings.force_cognitive_path:
        return PathDecision.COGNITIVE

    # 1. Always-fast tools (unless suspicious context)
    if tool_name in LOW_RISK_TOOLS:
        task_desc = context.get("task_description", "")
        if not _has_suspicious_context(task_desc):
            return PathDecision.FAST

    # 2. Always-cognitive tools
    if tool_name in HIGH_RISK_TOOLS:
        return PathDecision.COGNITIVE

    # 3. PII keys in arguments
    if any(k.lower() in _PII_ARGUMENT_KEYS for k in arguments):
        return PathDecision.COGNITIVE

    # 4. Suspicious context language
    task_desc = context.get("task_description", "")
    if _has_suspicious_context(task_desc):
        return PathDecision.COGNITIVE

    # 5. High-value payment
    if "amount" in arguments:
        try:
            if float(arguments["amount"]) > _PAYMENT_COGNITIVE_THRESHOLD:
                return PathDecision.COGNITIVE
        except (TypeError, ValueError):
            pass

    # 6. Default — let the fast path rules decide
    return PathDecision.FAST


def _has_suspicious_context(text: str) -> bool:
    return any(p.search(text) for p in _SUSPICIOUS_PATTERNS)
