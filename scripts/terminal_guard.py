#!/usr/bin/env python3
"""
Terminal guard for Sentinel.

This script evaluates a terminal command against Sentinel before execution.
It is designed to be called by shell hooks (PowerShell/Bash/CMD wrappers).
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import random
import re
import string
import sys
import time
import urllib.error
import urllib.request


DEFAULT_GATEWAY = "http://localhost:8000"
DEFAULT_ADMIN_KEY = "snl_admin_dev_changeme_replace_me"
AGENT_CACHE = pathlib.Path.home() / ".sentinel_terminal_agent.json"

ALLOW = 0
BLOCK = 2


def _http_json(method: str, url: str, payload: dict | None, headers: dict[str, str]) -> tuple[int, dict]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url=url, data=data, method=method)
    for k, v in headers.items():
        req.add_header(k, v)
    if payload is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            body = resp.read().decode("utf-8") or "{}"
            return resp.status, json.loads(body)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8") if e.fp else "{}"
        try:
            parsed = json.loads(body)
        except Exception:
            parsed = {"detail": body}
        return e.code, parsed


def _random_suffix(n: int = 6) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


def _load_cached_agent_key() -> str | None:
    if not AGENT_CACHE.exists():
        return None
    try:
        data = json.loads(AGENT_CACHE.read_text(encoding="utf-8"))
        return data.get("api_key")
    except Exception:
        return None


def _cache_agent_key(api_key: str) -> None:
    AGENT_CACHE.write_text(json.dumps({"api_key": api_key}), encoding="utf-8")


def _ensure_agent_key(gateway: str, admin_key: str) -> str:
    cached = _load_cached_agent_key()
    if cached:
        return cached

    payload = {
        "name": f"terminal-guard-{_random_suffix()}",
        "policy_group": "financial",
        "tenant_id": "system",
    }
    status, body = _http_json(
        "POST",
        f"{gateway}/v1/agents",
        payload,
        {"X-Sentinel-Agent-Key": admin_key},
    )
    if status not in (200, 201) or "api_key" not in body:
        raise RuntimeError(f"Could not create terminal guard agent: {body}")
    api_key = body["api_key"]
    _cache_agent_key(api_key)
    return api_key


def _derive_tool_call(command: str, cwd: str, shell: str) -> tuple[str, dict, dict]:
    normalized = command.strip()

    # Map common file-read commands to read_file so existing/read-focused policies apply.
    m = re.match(r"^\s*(cat|type|Get-Content|gc|more)\s+(.+?)\s*$", normalized, flags=re.IGNORECASE)
    if m:
        raw_path = m.group(2).strip().strip("\"'")
        return (
            "read_file",
            {"path": raw_path},
            {"task_description": f"Terminal file read from {shell} in {cwd}"},
        )

    # Generic terminal command tool for policy rules targeting shell commands.
    return (
        "run_terminal_command",
        {"command": normalized, "cwd": cwd, "shell": shell},
        {"task_description": f"Terminal command execution from {shell} in {cwd}"},
    )


def _poll_decision(gateway: str, api_key: str, decision_id: str) -> dict:
    for _ in range(30):
        status, body = _http_json(
            "GET",
            f"{gateway}/v1/decisions/{decision_id}",
            None,
            {"X-Sentinel-Agent-Key": api_key},
        )
        if status == 200 and body.get("status") != "pending":
            return body
        time.sleep(0.4)
    return {
        "verdict": "HUMAN_REVIEW",
        "reason": "Timed out waiting for Sentinel decision",
    }


def evaluate(command: str, cwd: str, shell: str, fail_closed: bool) -> int:
    gateway = os.getenv("SENTINEL_GATEWAY_URL", DEFAULT_GATEWAY).rstrip("/")
    admin_key = os.getenv("SENTINEL_ADMIN_KEY", DEFAULT_ADMIN_KEY)

    try:
        api_key = _ensure_agent_key(gateway, admin_key)
        tool_name, arguments, context = _derive_tool_call(command, cwd, shell)
        payload = {
            "tool_name": tool_name,
            "arguments": arguments,
            "session_id": f"term_{shell}",
            "context": {
                "task_description": context["task_description"],
                "conversation_history": [],
                "source_documents": [],
            },
            "metadata": {"request_id": f"term-{int(time.time() * 1000)}"},
        }

        status, body = _http_json(
            "POST",
            f"{gateway}/v1/tool-calls",
            payload,
            {"X-Sentinel-Agent-Key": api_key},
        )

        if status == 202 and body.get("decision_id"):
            body = _poll_decision(gateway, api_key, body["decision_id"])
        elif status == 429:
            body = {"verdict": "BLOCKED", "reason": "Rate limit exceeded"}
        elif status >= 400:
            raise RuntimeError(f"Sentinel request failed ({status}): {body}")

        verdict = str(body.get("verdict", "ALLOWED")).upper()
        reason = body.get("reason", "")

        if verdict == "ALLOWED":
            return ALLOW

        print(f"[Sentinel] BLOCKED: {verdict} - {reason}", file=sys.stderr)
        return BLOCK
    except Exception as exc:
        mode = "FAIL-CLOSED" if fail_closed else "FAIL-OPEN"
        print(f"[Sentinel] Guard error ({mode}): {exc}", file=sys.stderr)
        return BLOCK if fail_closed else ALLOW


def main() -> int:
    parser = argparse.ArgumentParser(description="Sentinel terminal guard")
    parser.add_argument("--command", required=True, help="Command line to evaluate")
    parser.add_argument("--cwd", default=os.getcwd(), help="Current working directory")
    parser.add_argument("--shell", default="unknown", help="Shell name")
    parser.add_argument("--fail-closed", action="store_true", help="Block when Sentinel is unreachable")
    args = parser.parse_args()
    return evaluate(args.command, args.cwd, args.shell, args.fail_closed)


if __name__ == "__main__":
    raise SystemExit(main())

