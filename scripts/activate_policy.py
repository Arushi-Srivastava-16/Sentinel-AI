#!/usr/bin/env python3
"""
CLI — activate a policy version in Redis.

Usage:
    python scripts/activate_policy.py --group financial --version 2.0.0
    python scripts/activate_policy.py --group financial --version 1.0.0 --tenant acme_corp

This script is the CLI equivalent of POST /v1/policies/{group}/activate.
Use it for Demo C, runbook procedures, or CI pipelines that need to
switch policy versions without an admin API key.

Environment:
    Reads .env / environment variables via gateway.config.Settings.
    Requires Redis to be running.
"""

from __future__ import annotations

import argparse
import asyncio
import sys


async def main(group: str, version: str, tenant_id: str) -> None:
    # Import here so the script fails fast with a clear message if deps missing
    try:
        from policies.loader import activate_policy_version, get_active_policy, list_available_policies
    except ImportError as e:
        print(f"ERROR: Cannot import policy modules. Run from repo root with venv active.\n{e}")
        sys.exit(1)

    # Show available versions
    available = list_available_policies(group)
    if not available:
        print(f"ERROR: No policy files found for group '{group}' in policies/examples/")
        print("Available groups:", set(p.get("policy_group") for p in _all_policies()))
        sys.exit(1)

    available_versions = [p["version"] for p in available]
    if version not in available_versions:
        print(f"ERROR: Version '{version}' not found for group '{group}'.")
        print(f"Available versions: {', '.join(available_versions)}")
        sys.exit(1)

    # Get current active version before switching
    current_policy = await get_active_policy(group, tenant_id=tenant_id)
    current_version = current_policy.get("version", "unknown") if current_policy else "none"

    # Activate the new version
    result = await activate_policy_version(group, version, tenant_id=tenant_id)
    if result is None:
        print(f"ERROR: Activation failed — version '{version}' not found.")
        sys.exit(1)

    print(f"Policy activated:")
    print(f"  Group:   {group}")
    print(f"  Tenant:  {tenant_id}")
    print(f"  Change:  {current_version}  →  {version}")
    print(f"  Rules:   {len(result.get('rules', []))} rules loaded")
    if result.get("description"):
        print(f"  Notes:   {result['description']}")


def _all_policies():
    """Helper — list all policies across all groups (for error messages)."""
    from pathlib import Path
    import yaml
    policies_dir = Path(__file__).parent.parent / "policies" / "examples"
    result = []
    for p in policies_dir.glob("*.yaml"):
        with open(p) as f:
            result.append(yaml.safe_load(f))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Activate a Sentinel policy version in Redis",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/activate_policy.py --group financial --version 1.0.0
  python scripts/activate_policy.py --group financial --version 2.0.0 --tenant acme_corp
        """,
    )
    parser.add_argument("--group", required=True, help="Policy group name (e.g. financial)")
    parser.add_argument("--version", required=True, help="Version to activate (e.g. 2.0.0)")
    parser.add_argument(
        "--tenant",
        default="system",
        help="Tenant ID scope (default: system — affects all tenants without their own override)",
    )

    args = parser.parse_args()
    asyncio.run(main(group=args.group, version=args.version, tenant_id=args.tenant))
