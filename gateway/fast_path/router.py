"""
Fast path orchestrator — runs all deterministic checks in order.

Check order (first match wins and short-circuits):
  1. Denylist   → BLOCKED   (critical severity rules first)
  2. Allowlist  → BLOCKED or HUMAN_REVIEW
  3. Threshold  → BLOCKED
  4. Regex      → BLOCKED, HUMAN_REVIEW, or cognitive_check
  5. Rate limit → 429 (separate from verdict flow)

Returns FastPathResult which tells the gateway what to do next.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from gateway.fast_path.allowlist import AllowlistRule, AllowlistResult, check_allowlist
from gateway.fast_path.denylist import DenylistRule, DenylistResult, check_denylist
from gateway.fast_path.threshold import ThresholdRule, ThresholdResult, check_threshold
from gateway.models.requests import DecisionPath, Verdict


@dataclass
class RegexRule:
    rule_id: str
    description: str
    severity: str
    applies_to_tools: list[str]
    argument_key: str
    pattern: str
    action: str   # "block" | "human_review" | "cognitive_check"
    _compiled: re.Pattern = field(init=False)

    def __post_init__(self) -> None:
        self._compiled = re.compile(self.pattern, re.IGNORECASE)

    def applies_to(self, tool_name: str) -> bool:
        return not self.applies_to_tools or tool_name in self.applies_to_tools

    def matches(self, arguments: dict[str, Any], context: dict[str, Any]) -> bool:
        if self.argument_key.startswith("__context."):
            field_name = self.argument_key[len("__context."):]
            val = context.get(field_name, "")
        else:
            val = arguments.get(self.argument_key, "")
        return bool(self._compiled.search(str(val)))


@dataclass
class FastPathResult:
    verdict: Verdict | None            # None = pass to cognitive path
    reason: str = ""
    rule_id: str = ""
    severity: str = ""
    needs_cognitive: bool = False      # True when a rule says "cognitive_check"
    path: DecisionPath = DecisionPath.FAST


@dataclass
class FastPathRules:
    denylist: list[DenylistRule] = field(default_factory=list)
    allowlist: list[AllowlistRule] = field(default_factory=list)
    threshold: list[ThresholdRule] = field(default_factory=list)
    regex: list[RegexRule] = field(default_factory=list)


def evaluate_fast_path(
    tool_name: str,
    arguments: dict[str, Any],
    context: dict[str, Any],
    rules: FastPathRules,
) -> FastPathResult:
    # 1. Denylist
    deny: DenylistResult = check_denylist(tool_name, arguments, context, rules.denylist)
    if deny.blocked:
        return FastPathResult(
            verdict=Verdict.BLOCKED,
            reason=deny.reason,
            rule_id=deny.rule_id,
            severity=deny.severity,
        )

    # 2. Allowlist
    allow: AllowlistResult = check_allowlist(tool_name, arguments, rules.allowlist)
    if allow.triggered:
        verdict = Verdict.BLOCKED if allow.action == "block" else Verdict.HUMAN_REVIEW
        return FastPathResult(
            verdict=verdict,
            reason=allow.reason,
            rule_id=allow.rule_id,
            severity=allow.severity,
        )

    # 3. Threshold
    thresh: ThresholdResult = check_threshold(tool_name, arguments, rules.threshold)
    if thresh.triggered:
        verdict = Verdict.BLOCKED if thresh.action == "block" else Verdict.HUMAN_REVIEW
        return FastPathResult(
            verdict=verdict,
            reason=thresh.reason,
            rule_id=thresh.rule_id,
            severity=thresh.severity,
        )

    # 4. Regex rules
    for rule in rules.regex:
        if not rule.applies_to(tool_name):
            continue
        if rule.matches(arguments, context):
            if rule.action == "block":
                return FastPathResult(
                    verdict=Verdict.BLOCKED,
                    reason=f"Rule '{rule.rule_id}': {rule.description}",
                    rule_id=rule.rule_id,
                    severity=rule.severity,
                )
            if rule.action == "human_review":
                return FastPathResult(
                    verdict=Verdict.HUMAN_REVIEW,
                    reason=f"Rule '{rule.rule_id}': {rule.description}",
                    rule_id=rule.rule_id,
                    severity=rule.severity,
                )
            if rule.action == "cognitive_check":
                return FastPathResult(
                    verdict=None,
                    needs_cognitive=True,
                    reason=f"Escalated by rule '{rule.rule_id}': {rule.description}",
                    rule_id=rule.rule_id,
                    severity=rule.severity,
                    path=DecisionPath.COGNITIVE,
                )

    # No rule triggered → allow
    return FastPathResult(verdict=Verdict.ALLOWED, reason="No policy rules triggered.")


# ---------------------------------------------------------------------------
# Factory — build FastPathRules from parsed policy YAML
# ---------------------------------------------------------------------------

def rules_from_policy(policy_rules: list[dict]) -> FastPathRules:
    from gateway.fast_path import allowlist as al_mod
    from gateway.fast_path import denylist as dl_mod
    from gateway.fast_path import threshold as th_mod

    regex_rules = []
    for rule in policy_rules:
        if rule.get("type") != "regex":
            continue
        cfg = rule.get("config", {})
        regex_rules.append(RegexRule(
            rule_id=rule["id"],
            description=rule.get("description", ""),
            severity=rule.get("severity", "medium"),
            applies_to_tools=rule.get("applies_to_tools", []),
            argument_key=cfg.get("argument_key", ""),
            pattern=cfg.get("pattern", ""),
            action=rule.get("action", "block"),
        ))

    return FastPathRules(
        denylist=dl_mod.rules_from_policy(policy_rules),
        allowlist=al_mod.rules_from_policy(policy_rules),
        threshold=th_mod.rules_from_policy(policy_rules),
        regex=regex_rules,
    )
