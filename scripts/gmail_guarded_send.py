#!/usr/bin/env python3
"""
Gmail sender with Sentinel pre-check.

This script demonstrates practical integration:
  1) Every Gmail action is first evaluated by Sentinel.
  2) Gmail call executes only when verdict is ALLOWED.

Usage examples:
  python scripts/gmail_guarded_send.py --mode draft --to user@gmail.com --subject "Test" --body "Hello"
  python scripts/gmail_guarded_send.py --mode send --to user@gmail.com --subject "Hi" --body "Body text"
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import pathlib
import random
import string
import urllib.error
import urllib.request
from email.message import EmailMessage

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.compose",
]

GATEWAY_URL = os.getenv("SENTINEL_GATEWAY_URL", "http://localhost:8000").rstrip("/")
ADMIN_KEY = os.getenv("SENTINEL_ADMIN_KEY", "snl_admin_dev_changeme_replace_me")
AGENT_CACHE = pathlib.Path("secrets/gmail/sentinel_agent.json")


def _http_json(method: str, url: str, payload: dict | None, headers: dict[str, str]) -> tuple[int, dict]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url=url, data=data, method=method)
    for k, v in headers.items():
        req.add_header(k, v)
    if payload is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8") if e.fp else "{}"
        try:
            return e.code, json.loads(body)
        except Exception:
            return e.code, {"detail": body}


def _random_suffix(n: int = 6) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


def _load_agent_key() -> str | None:
    if not AGENT_CACHE.exists():
        return None
    try:
        return json.loads(AGENT_CACHE.read_text(encoding="utf-8")).get("api_key")
    except Exception:
        return None


def _save_agent_key(key: str) -> None:
    AGENT_CACHE.parent.mkdir(parents=True, exist_ok=True)
    AGENT_CACHE.write_text(json.dumps({"api_key": key}, indent=2), encoding="utf-8")


def _ensure_agent_key() -> str:
    key = _load_agent_key()
    if key:
        return key
    payload = {
        "name": f"gmail-connector-{_random_suffix()}",
        "policy_group": "financial",
        "tenant_id": "system",
    }
    status, body = _http_json(
        "POST",
        f"{GATEWAY_URL}/v1/agents",
        payload,
        {"X-Sentinel-Agent-Key": ADMIN_KEY},
    )
    if status not in (200, 201) or "api_key" not in body:
        raise RuntimeError(f"Could not register connector agent: {status} {body}")
    _save_agent_key(body["api_key"])
    return body["api_key"]


def _check_with_sentinel(tool_name: str, arguments: dict, task_description: str) -> dict:
    agent_key = _ensure_agent_key()
    payload = {
        "tool_name": tool_name,
        "arguments": arguments,
        "session_id": "gmail_connector_session",
        "context": {
            "task_description": task_description,
            "conversation_history": [],
            "source_documents": [],
        },
        "metadata": {"request_id": f"gmail-{_random_suffix(10)}"},
    }
    status, body = _http_json(
        "POST",
        f"{GATEWAY_URL}/v1/tool-calls",
        payload,
        {"X-Sentinel-Agent-Key": agent_key},
    )
    if status == 200:
        return body
    if status == 202 and body.get("decision_id"):
        # For demo safety, treat pending as human review and do not execute.
        return {
            "verdict": "HUMAN_REVIEW",
            "reason": "Pending cognitive evaluation; execution paused in demo script.",
            "decision_id": body["decision_id"],
        }
    raise RuntimeError(f"Sentinel call failed: {status} {body}")


def _gmail_service():
    creds = Credentials.from_authorized_user_file("secrets/gmail/token.json", SCOPES)
    return build("gmail", "v1", credentials=creds)


def _build_raw_message(to: str, subject: str, body: str) -> str:
    msg = EmailMessage()
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
    return raw


def main() -> None:
    parser = argparse.ArgumentParser(description="Gmail send/draft with Sentinel pre-check")
    parser.add_argument("--mode", choices=["draft", "send"], default="draft")
    parser.add_argument("--to", required=True)
    parser.add_argument("--subject", required=True)
    parser.add_argument("--body", required=True)
    parser.add_argument("--attachment-name", default="")
    args = parser.parse_args()

    to_domain = args.to.split("@")[-1].lower() if "@" in args.to else ""
    tool_name = "gmail_create_draft" if args.mode == "draft" else "gmail_send_email"
    decision = _check_with_sentinel(
        tool_name=tool_name,
        arguments={
            "to": args.to,
            "to_domain": to_domain,
            "subject": args.subject,
            "body": args.body,
            "attachment_name": args.attachment_name,
        },
        task_description=f"Prepare outbound Gmail {args.mode}",
    )

    verdict = str(decision.get("verdict", "")).upper()
    if verdict != "ALLOWED":
        print(f"[Sentinel] {verdict}: {decision.get('reason', 'No reason provided')}")
        return

    svc = _gmail_service()
    raw = _build_raw_message(args.to, args.subject, args.body)

    if args.mode == "draft":
        out = svc.users().drafts().create(
            userId="me",
            body={"message": {"raw": raw}},
        ).execute()
        print("Draft created:", out.get("id"))
    else:
        out = svc.users().messages().send(
            userId="me",
            body={"raw": raw},
        ).execute()
        print("Email sent. Message ID:", out.get("id"))


if __name__ == "__main__":
    main()

