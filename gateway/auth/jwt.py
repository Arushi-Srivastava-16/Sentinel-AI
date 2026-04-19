"""
JWT signing and verification for Sentinel Gateway.

Agents exchange a long-lived API key for a short-lived RS256 JWT (15min TTL).
The JWT carries: agent_id, tenant_id, policy_group, session_id, issued_at, expires_at.

Key pair lifecycle:
  - On first startup, generate_key_pair() writes RSA-2048 keys to ./certs/
  - Keys are read from disk on every sign/verify call (no in-memory caching — safe restart)
  - In production, mount keys via Kubernetes Secret or External Secrets Operator

Token format (claims):
  {
    "sub":          "<agent_id>",
    "tenant_id":    "<tenant_id>",
    "policy_group": "<policy_group>",
    "session_id":   "<session_id>",
    "iat":          <unix timestamp>,
    "exp":          <unix timestamp + ttl_minutes * 60>
  }
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import structlog
from jose import JWTError, jwt
from jose.exceptions import ExpiredSignatureError

from gateway.config import settings

log = structlog.get_logger()


class JWTAuthError(Exception):
    """Raised when a JWT cannot be verified."""


def _private_key() -> str:
    path = Path(settings.jwt_private_key_path)
    if not path.exists():
        generate_key_pair()
    return path.read_text()


def _public_key() -> str:
    path = Path(settings.jwt_public_key_path)
    if not path.exists():
        generate_key_pair()
    return path.read_text()


def generate_key_pair() -> None:
    """
    Generate an RSA-2048 key pair and write to the configured paths.
    Called automatically on first use. Safe to call multiple times (no-op if keys exist).
    """
    private_path = Path(settings.jwt_private_key_path)
    public_path = Path(settings.jwt_public_key_path)

    if private_path.exists() and public_path.exists():
        return

    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.hazmat.backends import default_backend
    except ImportError:
        raise RuntimeError(
            "cryptography package required for JWT key generation. "
            "pip install 'python-jose[cryptography]'"
        )

    private_path.parent.mkdir(parents=True, exist_ok=True)
    public_path.parent.mkdir(parents=True, exist_ok=True)

    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend(),
    )

    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    private_path.write_bytes(private_pem)
    public_path.write_bytes(public_pem)
    # Restrict private key permissions
    os.chmod(private_path, 0o600)

    log.info(
        "jwt_key_pair_generated",
        private_key_path=str(private_path),
        public_key_path=str(public_path),
    )


def sign_jwt(
    agent_id: str,
    tenant_id: str,
    policy_group: str,
    session_id: str,
) -> str:
    """
    Sign and return a short-lived RS256 JWT for an authenticated agent.
    TTL is controlled by settings.jwt_ttl_minutes (default: 15).
    """
    now = int(time.time())
    ttl = settings.jwt_ttl_minutes * 60

    claims: dict[str, Any] = {
        "sub":          agent_id,
        "tenant_id":    tenant_id,
        "policy_group": policy_group,
        "session_id":   session_id,
        "iat":          now,
        "exp":          now + ttl,
    }

    token = jwt.encode(
        claims,
        _private_key(),
        algorithm=settings.jwt_algorithm,
    )
    log.info("jwt_issued", agent_id=agent_id, tenant_id=tenant_id, ttl_seconds=ttl)
    return token


def verify_jwt(token: str) -> dict[str, Any]:
    """
    Verify a JWT and return its decoded claims.
    Raises JWTAuthError on any verification failure.
    """
    try:
        claims = jwt.decode(
            token,
            _public_key(),
            algorithms=[settings.jwt_algorithm],
        )
        return claims
    except ExpiredSignatureError:
        raise JWTAuthError("JWT has expired.")
    except JWTError as e:
        raise JWTAuthError(f"Invalid JWT: {e}")
