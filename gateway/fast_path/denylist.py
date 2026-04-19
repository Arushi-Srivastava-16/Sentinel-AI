"""
Denylist checker — blocks requests that match known malicious patterns.

Patterns come from the active policy YAML (rule type: "denylist").
Supports match modes: exact, prefix, contains, regex.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any


class MatchMode(str, Enum):
    EXACT    = "exact"
    PREFIX   = "prefix"
    CONTAINS = "contains"
    REGEX    = "regex"


@dataclass
class DenylistRule:
    rule_id: str
    description: str
    severity: str
    applies_to_tools: list[str]   # empty = all tools
    argument_key: str
    match_mode: MatchMode
    patterns: list[str]

    # Pre-compiled regex patterns (only used when match_mode == REGEX)
    _compiled: list[re.Pattern] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.match_mode == MatchMode.REGEX:
            self._compiled = [re.compile(p, re.IGNORECASE) for p in self.patterns]

    def applies_to(self, tool_name: str) -> bool:
        return not self.applies_to_tools or tool_name in self.applies_to_tools

    def _get_value(self, arguments: dict[str, Any], context: dict[str, Any]) -> str | None:
        # Support __context.task_description style keys
        if self.argument_key.startswith("__context."):
            field = self.argument_key[len("__context."):]
            val = context.get(field, "")
        else:
            val = arguments.get(self.argument_key)
        return str(val) if val is not None else None

    def matches(self, arguments: dict[str, Any], context: dict[str, Any]) -> bool:
        value = self._get_value(arguments, context)
        if value is None:
            return False

        if self.match_mode == MatchMode.EXACT:
            return value in self.patterns
        if self.match_mode == MatchMode.PREFIX:
            return any(value.startswith(p) for p in self.patterns)
        if self.match_mode == MatchMode.CONTAINS:
            return any(p in value for p in self.patterns)
        if self.match_mode == MatchMode.REGEX:
            return any(r.search(value) for r in self._compiled)
        return False


@dataclass
class DenylistResult:
    blocked: bool
    rule_id: str = ""
    reason: str = ""
    severity: str = ""


def check_denylist(
    tool_name: str,
    arguments: dict[str, Any],
    context: dict[str, Any],
    rules: list[DenylistRule],
) -> DenylistResult:
    """
    Check a tool call against all denylist rules.
    Returns the first matching rule (rules are checked in order).
    """
    for rule in rules:
        if not rule.applies_to(tool_name):
            continue
        if rule.matches(arguments, context):
            return DenylistResult(
                blocked=True,
                rule_id=rule.rule_id,
                reason=f"Blocked by rule '{rule.rule_id}': {rule.description}",
                severity=rule.severity,
            )
    return DenylistResult(blocked=False)


# ---------------------------------------------------------------------------
# Factory — build rules from parsed policy YAML
# ---------------------------------------------------------------------------

def rules_from_policy(policy_rules: list[dict]) -> list[DenylistRule]:
    """Build DenylistRule objects from the 'rules' list in a policy YAML dict."""
    result = []
    for rule in policy_rules:
        if rule.get("type") != "denylist":
            continue
        cfg = rule.get("config", {})
        result.append(DenylistRule(
            rule_id=rule["id"],
            description=rule.get("description", ""),
            severity=rule.get("severity", "medium"),
            applies_to_tools=rule.get("applies_to_tools", []),
            argument_key=cfg.get("argument_key", ""),
            match_mode=MatchMode(cfg.get("match_mode", "exact")),
            patterns=cfg.get("patterns", []),
        ))
    return result
