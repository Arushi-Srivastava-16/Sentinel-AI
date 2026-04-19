"""
Allowlist checker — blocks requests where an argument is NOT in the approved list.
Also handles the "always_match" sentinel for rules that always require review.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class AllowlistRule:
    rule_id: str
    description: str
    severity: str
    applies_to_tools: list[str]
    argument_key: str        # "__always_match" triggers unconditionally
    allowed_values: list[str]
    action: str              # "block" | "human_review"

    def applies_to(self, tool_name: str) -> bool:
        return not self.applies_to_tools or tool_name in self.applies_to_tools

    def matches(self, arguments: dict[str, Any]) -> bool:
        if self.argument_key == "__always_match":
            return True
        value = arguments.get(self.argument_key)
        if value is None:
            return True  # key missing = not in allowlist = block
        return str(value) not in self.allowed_values


@dataclass
class AllowlistResult:
    triggered: bool
    action: str = ""    # "block" | "human_review"
    rule_id: str = ""
    reason: str = ""
    severity: str = ""


def check_allowlist(
    tool_name: str,
    arguments: dict[str, Any],
    rules: list[AllowlistRule],
) -> AllowlistResult:
    for rule in rules:
        if not rule.applies_to(tool_name):
            continue
        if rule.matches(arguments):
            return AllowlistResult(
                triggered=True,
                action=rule.action,
                rule_id=rule.rule_id,
                reason=f"Rule '{rule.rule_id}': {rule.description}",
                severity=rule.severity,
            )
    return AllowlistResult(triggered=False)


def rules_from_policy(policy_rules: list[dict]) -> list[AllowlistRule]:
    result = []
    for rule in policy_rules:
        if rule.get("type") != "allowlist":
            continue
        cfg = rule.get("config", {})
        result.append(AllowlistRule(
            rule_id=rule["id"],
            description=rule.get("description", ""),
            severity=rule.get("severity", "medium"),
            applies_to_tools=rule.get("applies_to_tools", []),
            argument_key=cfg.get("argument_key", ""),
            allowed_values=cfg.get("patterns", []),
            action=rule.get("action", "block"),
        ))
    return result
