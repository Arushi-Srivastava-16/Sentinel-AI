"""
Threshold checker — blocks requests where a numeric argument exceeds a limit.
Used for payment amount limits, etc.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ThresholdRule:
    rule_id: str
    description: str
    severity: str
    applies_to_tools: list[str]
    argument_key: str
    max_value: float
    action: str = "block"

    def applies_to(self, tool_name: str) -> bool:
        return not self.applies_to_tools or tool_name in self.applies_to_tools


@dataclass
class ThresholdResult:
    triggered: bool
    action: str = ""
    rule_id: str = ""
    reason: str = ""
    severity: str = ""


def check_threshold(
    tool_name: str,
    arguments: dict[str, Any],
    rules: list[ThresholdRule],
) -> ThresholdResult:
    for rule in rules:
        if not rule.applies_to(tool_name):
            continue
        value = arguments.get(rule.argument_key)
        if value is None:
            continue
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        if numeric > rule.max_value:
            return ThresholdResult(
                triggered=True,
                action=rule.action,
                rule_id=rule.rule_id,
                reason=(
                    f"Rule '{rule.rule_id}': {rule.description} "
                    f"(value {numeric} > max {rule.max_value})"
                ),
                severity=rule.severity,
            )
    return ThresholdResult(triggered=False)


def rules_from_policy(policy_rules: list[dict]) -> list[ThresholdRule]:
    result = []
    for rule in policy_rules:
        if rule.get("type") != "threshold":
            continue
        cfg = rule.get("config", {})
        result.append(ThresholdRule(
            rule_id=rule["id"],
            description=rule.get("description", ""),
            severity=rule.get("severity", "medium"),
            applies_to_tools=rule.get("applies_to_tools", []),
            argument_key=cfg.get("argument_key", "amount"),
            max_value=float(cfg.get("max_value", 0)),
            action=rule.get("action", "block"),
        ))
    return result
