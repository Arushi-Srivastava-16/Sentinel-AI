"""
Policy YAML loader and version resolver.

Loads policy files from disk, caches the active version per policy_group in Redis,
and exposes the FastPathRules built from the active policy.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from gateway.config import settings
from gateway.fast_path.router import FastPathRules, rules_from_policy

POLICIES_DIR = Path(__file__).parent / "examples"
# Namespaced by tenant so different tenants can run different active policy versions.
# Falls back to the "system" tenant key when tenant_id is not provided (e.g. CLI scripts).
_ACTIVE_POLICY_KEY = "policy:active:{tenant_id}:{group}"
_SYSTEM_TENANT = "system"


def load_policy_file(path: Path) -> dict[str, Any]:
    with open(path) as f:
        return yaml.safe_load(f)


def list_available_policies(policy_group: str) -> list[dict[str, Any]]:
    """Return all policy dicts for a given policy_group, sorted by version."""
    result = []
    for p in POLICIES_DIR.glob("*.yaml"):
        data = load_policy_file(p)
        if data.get("policy_group") == policy_group:
            result.append(data)
    result.sort(key=lambda d: tuple(int(x) for x in d["version"].split(".")))
    return result


async def get_active_policy(
    policy_group: str,
    tenant_id: str = _SYSTEM_TENANT,
) -> dict[str, Any] | None:
    """
    Get the currently active policy for a group + tenant.
    Checks tenant-scoped key first, then falls back to system-level key,
    then falls back to latest version on disk.
    """
    from shared.redis_client import rate_limit_client

    redis = rate_limit_client()
    try:
        # Try tenant-scoped key first
        for tid in (tenant_id, _SYSTEM_TENANT):
            raw = await redis.get(_ACTIVE_POLICY_KEY.format(tenant_id=tid, group=policy_group))
            if raw:
                version = raw
                policies = list_available_policies(policy_group)
                for p in policies:
                    if p["version"] == version:
                        return p
    except Exception:
        pass
    finally:
        try:
            await redis.aclose()
        except Exception:
            pass

    # Fallback: latest version on disk
    policies = list_available_policies(policy_group)
    return policies[-1] if policies else None


async def activate_policy_version(
    policy_group: str,
    version: str,
    tenant_id: str = _SYSTEM_TENANT,
) -> dict[str, Any] | None:
    """
    Set the active policy version for a group + tenant in Redis.
    Returns the activated policy dict, or None if version not found.
    """
    from shared.redis_client import rate_limit_client

    policies = list_available_policies(policy_group)
    target = next((p for p in policies if p["version"] == version), None)
    if not target:
        return None

    redis = rate_limit_client()
    try:
        await redis.set(
            _ACTIVE_POLICY_KEY.format(tenant_id=tenant_id, group=policy_group),
            version,
        )
        # Bust the fast-path rules cache
        _get_fast_path_rules.cache_clear()
    finally:
        await redis.aclose()

    # Update Prometheus gauge
    from gateway.metrics import policy_version_active
    policy_version_active.labels(policy_group=policy_group, version=version).set(1)

    return target


async def get_fast_path_rules_for_group(
    policy_group: str,
    tenant_id: str = _SYSTEM_TENANT,
) -> FastPathRules:
    """Load fast path rules from the active policy for a policy group + tenant."""
    policy = await get_active_policy(policy_group, tenant_id=tenant_id)
    if policy is None:
        return FastPathRules()
    return rules_from_policy(policy.get("rules", []))


# Sync version used by tests / CLI scripts
@lru_cache(maxsize=32)
def _get_fast_path_rules(policy_group: str, version: str) -> FastPathRules:
    policies = list_available_policies(policy_group)
    target = next((p for p in policies if p["version"] == version), None)
    if not target:
        return FastPathRules()
    return rules_from_policy(target.get("rules", []))
