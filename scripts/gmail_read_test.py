#!/usr/bin/env python3
"""
Simple Gmail read test using OAuth token.json.

Usage:
  python scripts/gmail_read_test.py
"""

from __future__ import annotations

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.compose",
]


def main() -> None:
    creds = Credentials.from_authorized_user_file("secrets/gmail/token.json", SCOPES)
    svc = build("gmail", "v1", credentials=creds)
    resp = svc.users().messages().list(userId="me", maxResults=5).execute()
    msgs = resp.get("messages", [])
    print("messages_found:", len(msgs))
    for m in msgs[:3]:
        print(" -", m.get("id"))


if __name__ == "__main__":
    main()

